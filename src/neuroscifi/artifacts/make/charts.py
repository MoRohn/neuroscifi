"""Charts: line, bar (grouped, stacked), scatter, histogram, heatmap and small multiples, as SVG.

Every chart follows the reading contract (DESIGN §6, ADR-074) exactly:
- one `<g class="plot">` holds the marks (small multiples: one per `<g class="panel">`);
- each axis is `<g class="axis x">` / `<g class="axis y">` (heatmap ramp: `<g class="axis z">`) holding
  `<g class="tick" transform="translate(X,Y)">` whose `<text>` is the tick's value as a plain decimal number, an
  ISO 8601 date, or a category label (cut labels carry the full label in a `<title>` child);
  each continuous axis also holds a `<line class="domain">` spanning the plot;
- marks: `<polyline class="series" points>` (line), `<circle class="mark">` (scatter), `<rect class="mark">`
  (bars, histogram bins, heatmap cells), each series inside `<g class="series" data-name="...">`;
- the source data is never written onto marks; it is embedded once, as JSON, inside `<metadata>`.

Layout is decided by stated rules: tick counts follow the plot's length (one per ~90 px across, ~48 px up); tick
labels that would overlap by the width model are first given fewer ticks (continuous axes), then rotated by 45
degrees; category labels are never thinned and never cut unless the spec asks (`options.abbreviate`, the full label
then in `<title>`): the picture grows taller for long rotated labels.
"""
from __future__ import annotations

import bisect
import math
from decimal import Decimal

from .scales import Fit, Scale, band, decimals_of, linear_ticks, log_ticks, ticks_for, to_datetime
from .svg_base import (AXIS, GRID, MUTED, SERIES_STYLES, abbreviate, bar_fill, contrast, el, flag_color, flag_of, gray_hex,
                       head, marker, num, patterns, scope_id, text_el, text_width)

W_DEFAULT, H_DEFAULT = 640, 400
TITLE_SIZE, LEGEND_SIZE, TICK_SIZE, LABEL_SIZE = 16, 12, 11, 12
MAX_SERIES = 24
MAX_POINTS = 200_000
METADATA_MAX = 256 << 10

# --- schemas ----------------------------------------------------------------------------------------------------

STR = {"type": "string", "maxLength": 200}
UNIT = {"type": ["string", "null"], "maxLength": 40}
FLAG = {"enum": ["none", "warning", "danger"]}
INDEXES = {"type": "array", "maxItems": 10000, "items": {"type": "integer", "minimum": 0}}


def axis_schema(scales):
    return {"type": "object", "additionalProperties": False, "required": ["label"],
            "properties": {"label": STR, "unit": UNIT, "scale": {"enum": list(scales)}}}


POINT = {"type": "array", "minItems": 2, "maxItems": 2, "items": {"type": ["number", "null"]}}
XY_SERIES = {"type": "object", "additionalProperties": False, "required": ["name", "points"],
             "properties": {"name": STR, "points": {"type": "array", "maxItems": 100_000, "items": POINT},
                            "flag": FLAG, "warning": INDEXES, "danger": INDEXES}}
XY_DATA = {"type": "object", "additionalProperties": False, "required": ["x", "y", "series"],
           "properties": {"x": axis_schema(("linear", "log", "time")), "y": axis_schema(("linear", "log")),
                          "series": {"type": "array", "minItems": 1, "maxItems": MAX_SERIES, "items": XY_SERIES}}}
BAR_SERIES = {"type": "object", "additionalProperties": False, "required": ["name", "values"],
              "properties": {"name": STR, "values": {"type": "array", "maxItems": 2000,
                                                     "items": {"type": ["number", "null"]}},
                             "flag": FLAG, "warning": INDEXES, "danger": INDEXES}}
BAR_DATA = {"type": "object", "additionalProperties": False, "required": ["categories", "x", "y", "series"],
            "properties": {"categories": {"type": "array", "minItems": 1, "maxItems": 2000, "items": STR},
                           "x": axis_schema(("band",)), "y": axis_schema(("linear",)),
                           "series": {"type": "array", "minItems": 1, "maxItems": MAX_SERIES, "items": BAR_SERIES}}}
HIST_DATA = {"type": "object", "additionalProperties": False, "required": ["x", "values"],
             "properties": {"x": axis_schema(("linear",)), "y": axis_schema(("linear",)),
                            "name": STR,
                            "values": {"type": "array", "minItems": 1, "maxItems": 200_000,
                                       "items": {"type": "number"}}}}
HEAT_DATA = {"type": "object", "additionalProperties": False, "required": ["x", "y", "z", "values"],
             "properties": {
                 "x": {"type": "object", "additionalProperties": False, "required": ["label", "categories"],
                       "properties": {"label": STR, "unit": UNIT,
                                      "categories": {"type": "array", "minItems": 1, "maxItems": 200, "items": STR}}},
                 "y": {"type": "object", "additionalProperties": False, "required": ["label", "categories"],
                       "properties": {"label": STR, "unit": UNIT,
                                      "categories": {"type": "array", "minItems": 1, "maxItems": 200, "items": STR}}},
                 "z": axis_schema(("linear",)),
                 "values": {"type": "array", "minItems": 1, "maxItems": 200,
                            "items": {"type": "array", "minItems": 1, "maxItems": 200,
                                      "items": {"type": ["number", "null"]}}}}}

HISTORY = {"type": "array", "maxItems": 12,
           "items": {"type": "object", "additionalProperties": False, "required": ["round", "rule", "change"],
                     "properties": {"round": {"type": "integer", "minimum": 1, "maximum": 3},
                                    "rule": {"type": "string", "maxLength": 64},
                                    "change": {"type": "string", "maxLength": 300}}}}
OPTIONS = {"type": "object", "additionalProperties": False, "properties": {
    "width": {"type": "integer", "minimum": 240, "maximum": 2400},
    "height": {"type": "integer", "minimum": 180, "maximum": 2400},
    "x_labels": {"enum": ["auto", "horizontal", "rotated"]},
    "x_tick_every": {"type": "integer", "minimum": 1, "maximum": 50},
    "abbreviate": {"type": "integer", "minimum": 4, "maximum": 60},
    "markers": {"enum": ["auto", "on", "off"]},
    "y_from_zero": {"type": "boolean"},
    "y_min": {"type": "number"}, "y_max": {"type": "number"},
    "x_min": {"type": "number"}, "x_max": {"type": "number"},
    "mode": {"enum": ["grouped", "stacked"]},
    "bins": {"type": "integer", "minimum": 1, "maximum": 500},
    "bin_width": {"type": "number", "minimum": 0},
    "columns": {"type": "integer", "minimum": 1, "maximum": 8},
    "print_values": {"enum": ["auto", "on", "off"]},
    "aggregate": {"type": "object", "additionalProperties": False, "required": ["rule", "bins"],
                  "properties": {"rule": {"enum": ["mean", "min_max"]},
                                 "bins": {"type": "integer", "minimum": 2, "maximum": 4000}}},
    "history": HISTORY}}


# --- small helpers ----------------------------------------------------------------------------------------------

def plain(value: float, digits: int = 6) -> str:
    """A number in words-friendly plain decimal: up to `digits` significant digits, no exponent, no locale."""
    if value == 0:
        return "0"
    text = format(Decimal(f"{value:.{digits}g}"), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def axis_label(axis: dict) -> str:
    unit = axis.get("unit")
    return f"{axis['label']} ({unit})" if unit else str(axis["label"])


def value_words(value: float, kind: str) -> str:
    if kind == "time":
        return to_datetime(value).strftime("%Y-%m-%dT%H:%M:%SZ")
    return plain(value)


class Parts(list):
    def add(self, *items):
        self.extend(items)


def _style(i: int):
    return SERIES_STYLES[i % len(SERIES_STYLES)]


# --- title and legend -------------------------------------------------------------------------------------------

def title_block(width: float, title: str) -> tuple[str, float]:
    shown, cut = abbreviate(title, width - 32, TITLE_SIZE)
    return text_el(16, 28, shown, size=TITLE_SIZE, weight="600", cls="title", full=title if cut else None), 44.0


def legend_block(width: float, top: float, keys: list[tuple[str, str]]) -> tuple[str, float]:
    """`keys`: (name, glyph svg drawn at origin 0..24 x -6..6, flag or None). Rows wrap at the width; returns
    (svg, bottom). A flagged series' key carries the flag's class (its hue belongs to the flag)."""
    if not keys:
        return "", top
    out, x, y = [], 16.0, top + 10
    for name, glyph, flag in keys:
        shown, cut = abbreviate(name, min(220.0, width - 64), LEGEND_SIZE)
        w = 30 + text_width(shown, LEGEND_SIZE) + 18
        if x > 16 and x + w > width - 16:
            x, y = 16.0, y + 20
        out.append(el("g", (("class", "key" + (f" {flag}" if flag else "")), ("data-name", name), ("transform", f"translate({num(x)},{num(y)})")),
                      glyph + text_el(30, 4, shown, size=LEGEND_SIZE, full=name if cut else None)))
        x += w
    return el("g", (("class", "legend"),), "".join(out)), y + 14


def line_key(i: int, color: str, with_marker: bool) -> str:
    dash, mark, _ = _style(i)
    body = el("line", (("x1", "0"), ("y1", "0"), ("x2", "24"), ("y2", "0"), ("stroke", color), ("stroke-width", "2"),
                       ("stroke-dasharray", dash or None), ("stroke-linecap", "round")))
    if with_marker:
        body += marker(mark, 12, 0, color, 3.5)
    return body


def point_key(i: int, color: str) -> str:
    return scatter_glyph(i, 12, 0, color, cls="marker")


def bar_key(sid: str, i: int, color: str) -> str:
    fill, opacity = bar_fill(sid, _style(i)[2])
    return el("rect", (("x", "4"), ("y", "-6"), ("width", "16"), ("height", "12"), ("fill", fill),
                       ("fill-opacity", opacity), ("stroke", color), ("stroke-width", "1")))


def scatter_glyph(i: int, x: float, y: float, color: str, cls: str = "mark") -> str:
    """A scatter point: the circle (class `cls`) sits exactly on the value; styles 2-5 add a glyph around it."""
    kind = _style(i)[1]
    if kind == "circle":
        return el("circle", (("class", cls), ("cx", num(x)), ("cy", num(y)), ("r", "3.5"), ("fill", color)))
    if kind == "ring":
        return el("circle", (("class", cls), ("cx", num(x)), ("cy", num(y)), ("r", "4"), ("fill", "var(--nsf-paper)"),
                             ("stroke", color), ("stroke-width", "1.5")))
    dot = el("circle", (("class", cls), ("cx", num(x)), ("cy", num(y)), ("r", "1.6"), ("fill", color)))
    return marker(kind, x, y, color, 4.6, cls="glyph") + dot


# --- axes -------------------------------------------------------------------------------------------------------

def _fits_horizontal(positions: list[float], labels: list[str], size: float, gap: float = 8.0) -> bool:
    for i in range(1, len(positions)):
        need = (text_width(labels[i - 1], size) + text_width(labels[i], size)) / 2 + gap
        if abs(positions[i] - positions[i - 1]) < need:
            return False
    return True


class XAxis:
    """A planned x axis: positions and labels of its ticks, rotation, and the space it needs below the plot."""

    def __init__(self, positions, labels, fulls, rotated, values=None):
        self.positions, self.labels, self.fulls, self.rotated = positions, labels, fulls, rotated
        self.values = values

    def height(self) -> float:
        if not self.labels:
            return 8
        if self.rotated:
            return 14 + max(text_width(t, TICK_SIZE) for t in self.labels) * 0.7072 + 6
        return 22

    def left_overhang(self, x0: float) -> float:
        """How far the first label reaches left of x0 (so the margin can grow)."""
        if not self.labels:
            return 0
        w = text_width(self.labels[0], TICK_SIZE)
        reach = w * 0.7072 + 4 if self.rotated else w / 2
        return max(0.0, reach - (self.positions[0] - x0))

    def right_overhang(self, x1: float) -> float:
        if not self.labels or self.rotated:
            return 0
        return max(0.0, text_width(self.labels[-1], TICK_SIZE) / 2 - (x1 - self.positions[-1]))

    def svg(self, y1: float, x0: float, x1: float, label: str, label_y: float, domain: bool = True) -> str:
        ticks = []
        for pos, text, full in zip(self.positions, self.labels, self.fulls):
            if self.rotated:
                t = text_el(4, 12, text, size=TICK_SIZE, anchor="end", rotate=-45, opacity=MUTED, full=full)
            else:
                t = text_el(0, 18, text, size=TICK_SIZE, anchor="middle", opacity=MUTED, full=full)
            ticks.append(el("g", (("class", "tick"), ("transform", f"translate({num(pos)},{num(y1)})")),
                            el("line", (("y2", "5"), ("stroke", "currentColor"), ("stroke-opacity", num(AXIS)))) + t))
        dom = el("line", (("class", "domain"), ("x1", num(x0)), ("y1", num(y1)), ("x2", num(x1)), ("y2", num(y1)),
                          ("stroke", "currentColor"), ("stroke-opacity", num(AXIS)))) if domain else ""
        lab = text_el((x0 + x1) / 2, label_y, label, size=LABEL_SIZE, anchor="middle", cls="label", opacity=MUTED)
        return el("g", (("class", "axis x"),), dom + "".join(ticks) + lab)


def plan_x_continuous(kind, lo, hi, x0, x1, opts, *, nice=True, per_tick=90):
    """Ticks for a continuous x axis that fit horizontally (fewer ticks first), else rotated (unless pinned)."""
    mode = opts.get("x_labels", "auto")
    every = int(opts.get("x_tick_every", 1))
    target = max(2, round((x1 - x0) / per_tick))
    ticks = None
    for t in range(target, 1, -1):
        ticks = ticks_for(kind, lo, hi, t, nice=nice)
        values, labels = _thin(ticks.values, every), _thin(ticks.labels, every)
        scale = Scale(ticks.lo, ticks.hi, x0, x1, log=(kind == "log"))
        pos = [scale(v) for v in values]
        if mode != "auto" or _fits_horizontal(pos, list(labels), TICK_SIZE):
            break
    values, labels = _thin(ticks.values, every), _thin(ticks.labels, every)
    scale = Scale(ticks.lo, ticks.hi, x0, x1, log=(kind == "log"))
    pos = [scale(v) for v in values]
    rotated = mode == "rotated" or (mode == "auto" and not _fits_horizontal(pos, list(labels), TICK_SIZE))
    return ticks, XAxis(pos, list(labels), [None] * len(labels), rotated, list(values))


def _thin(items, every):
    items = list(items)
    if every <= 1 or len(items) <= 2:
        return items
    kept = items[::every]
    return kept if len(kept) >= 2 else [items[0], items[-1]]


def plan_x_band(categories, x0, x1, opts):
    """Category ticks: horizontal if they fit, else rotated, then cut to fit (full label in <title>)."""
    mode = opts.get("x_labels", "auto")
    centres, _ = band(len(categories), x0, x1)
    spacing = (x1 - x0) / max(1, len(categories))
    limit = opts.get("abbreviate")
    labels, fulls = [], []
    for c in categories:
        shown, cut = (abbreviate(c, text_width("M", TICK_SIZE) * limit, TICK_SIZE) if limit else (c, False))
        labels.append(shown)
        fulls.append(c if cut else None)
    rotated = mode == "rotated" or (mode == "auto" and not _fits_horizontal(centres, labels, TICK_SIZE))
    if rotated and mode == "auto" and spacing < TICK_SIZE * 1.25:
        raise Fit(f"{len(categories)} categories do not fit {int(x1 - x0)} px with a readable label each: "
                  "make the chart wider (options.width) or show fewer categories")
    # Labels keep their full text (a reader reads the category from the tick's text); long rotated labels make the
    # picture taller instead (Frame.plot_box). Only `options.abbreviate`, asked for explicitly, cuts them.
    return XAxis(centres, labels, fulls, rotated)


def y_axis_svg(ticks, scale, x0, x1, y0, y1, label, label_x, *, grid=True, cls="axis y"):
    out_ticks, grid_lines = [], []
    for v, text in zip(ticks.values, ticks.labels):
        y = scale(v)
        out_ticks.append(el("g", (("class", "tick"), ("transform", f"translate({num(x0)},{num(y)})")),
                            el("line", (("x2", "-5"), ("stroke", "currentColor"), ("stroke-opacity", num(AXIS))))
                            + text_el(-8, TICK_SIZE * 0.35, text, size=TICK_SIZE, anchor="end", opacity=MUTED)))
        if grid:
            zero = (v == 0 and ticks.lo < 0 < ticks.hi)
            grid_lines.append(el("line", (("class", "zero" if zero else None), ("x1", num(x0)), ("y1", num(y)),
                                          ("x2", num(x1)), ("y2", num(y)), ("stroke", "currentColor"),
                                          ("stroke-opacity", num(AXIS if zero else GRID)))))
    dom = el("line", (("class", "domain"), ("x1", num(x0)), ("y1", num(y0)), ("x2", num(x0)), ("y2", num(y1)),
                      ("stroke", "currentColor"), ("stroke-opacity", num(AXIS))))
    lab = text_el(label_x, (y0 + y1) / 2, label, size=LABEL_SIZE, anchor="middle", cls="label", opacity=MUTED,
                  rotate=-90)
    g = el("g", (("class", cls),), dom + "".join(out_ticks) + lab)
    return (el("g", (("class", "grid"),), "".join(grid_lines)) if grid_lines else ""), g


def y_label_room(ticks) -> float:
    return max(text_width(t, TICK_SIZE) for t in ticks.labels) + 12


# --- shared frame -----------------------------------------------------------------------------------------------

class Frame:
    """The canvas split into title, legend, plot and axes, decided in passes (labels change margins)."""

    def __init__(self, spec, keys):
        o = spec.options
        self.W = float(o.get("width", W_DEFAULT))
        self.H = float(o.get("height", H_DEFAULT))
        self.title_svg, top = title_block(self.W, spec.title or "")
        self.legend_svg, top = legend_block(self.W, top, keys)
        self.top = top + 12
        self.sid = scope_id(spec.spec, spec.to_json())

    def plot_box(self, left, bottom, right=20.0):
        if self.H - bottom - self.top < 160:        # long labels below the plot: the picture grows taller
            self.H = float(self.top + bottom + 160)
        x0, x1, y0, y1 = left, self.W - right, self.top, self.H - bottom
        if x1 - x0 < 80 or y1 - y0 < 60:
            raise Fit("the chart is too small for its labels: make it larger (options.width, options.height)")
        return x0, x1, y0, y1


def _y_domain(values, opts, *, from_zero, log=False):
    finite = [v for v in values if v is not None and math.isfinite(v)]
    if log:
        finite = [v for v in finite if v > 0]
    if not finite:
        raise Fit("there are no values to draw")
    lo, hi = min(finite), max(finite)
    notes = []
    if from_zero and not log:
        lo, hi = min(lo, 0.0), max(hi, 0.0)
    if "y_min" in opts:
        if opts["y_min"] <= lo:
            lo = float(opts["y_min"])
        else:
            notes.append("y_min is above some values; the axis starts at the smallest value instead")
    if "y_max" in opts:
        if opts["y_max"] >= hi:
            hi = float(opts["y_max"])
        else:
            notes.append("y_max is below some values; the axis ends at the largest value instead")
    return lo, hi, notes


def _y_ticks(kind, lo, hi, y0, y1, *, nice=True):
    target = max(2, round((y1 - y0) / 48))
    if kind == "log":
        return log_ticks(lo, hi, target)
    return linear_ticks(lo, hi, target, nice=nice)


def finish(frame, desc, table, body, defs="", css=""):
    title = frame.title_text
    meta = table if len(str(table)) <= METADATA_MAX else None
    if meta is None:
        desc += " The source table is not embedded: it is larger than the limit."
    return head(frame.sid, frame.form, frame.W, frame.H, title, desc, meta, css, defs) + body + "</svg>"


def describe(spec, words: str) -> str:
    lead = (spec.description or "").strip()
    return (lead + " " if lead else "") + words


# --- aggregation (stated in the description) --------------------------------------------------------------------

def aggregate(points, rule: str, bins: int):
    """Equal-width bins of x over the series' x range, [lo + i w, lo + (i+1) w), the last bin closed; empty bins
    are skipped. "mean": one point per bin at (mean x, mean y). "min_max": two points per bin at (mean x, min y)
    then (mean x, max y)."""
    pts = [(x, y) for x, y in points if x is not None and y is not None]
    if not pts:
        return []
    lo = min(p[0] for p in pts)
    hi = max(p[0] for p in pts)
    groups: dict[int, list] = {}
    for x, y in pts:
        i = bins - 1 if hi == lo else min(bins - 1, int((x - lo) / (hi - lo) * bins))
        groups.setdefault(i, []).append((x, y))
    out = []
    for i in sorted(groups):
        g = groups[i]
        mx = sum(p[0] for p in g) / len(g)
        if rule == "mean":
            out.append((mx, sum(p[1] for p in g) / len(g)))
        else:
            out.append((mx, min(p[1] for p in g)))
            out.append((mx, max(p[1] for p in g)))
    return out


AGG_WORDS = {"mean": "each bin is drawn at the mean of its x values and the mean of its y values",
             "min_max": "each bin is drawn as two points at the mean of its x values: its smallest and its largest "
                        "y value"}


# --- line and scatter -------------------------------------------------------------------------------------------

def _xy(spec, form):
    d, o = spec.data, spec.options
    xk, yk = d["x"].get("scale", "linear"), d["y"].get("scale", "linear")
    series = d["series"]
    total = sum(len(s["points"]) for s in series)
    if total > MAX_POINTS:
        raise Fit(f"{total} points is more than the {MAX_POINTS} a chart draws: aggregate first")
    notes = []
    agg = o.get("aggregate")
    drawn = []
    for s in series:
        pts = [(p[0], p[1]) for p in s["points"]]
        if agg:
            pts = aggregate(pts, agg["rule"], agg["bins"])
        drawn.append(pts)
    xs = [x for pts in drawn for x, y in pts if x is not None and y is not None]
    ys = [y for pts in drawn for x, y in pts if x is not None and y is not None]
    if xk == "log":
        xs = [x for x in xs if x > 0]
    if not xs:
        raise Fit("there are no complete points to draw")
    skipped = sum(1 for pts in drawn for x, y in pts if x is None or y is None
                  or (xk == "log" and x is not None and x <= 0) or (yk == "log" and y is not None and y <= 0))
    if skipped:
        notes.append(f"{skipped} points with a missing value (or not above zero on a log axis) are not drawn")
    ylo, yhi, n2 = _y_domain(ys, o, from_zero=bool(o.get("y_from_zero", False)), log=(yk == "log"))
    notes += n2
    xlo, xhi = min(xs), max(xs)
    if "x_min" in o and (xk != "log" or o["x_min"] > 0):    # the x axis covers at least [x_min, x_max]
        xlo = min(xlo, float(o["x_min"]))
    if "x_max" in o:
        xhi = max(xhi, float(o["x_max"]))

    line = form == "chart.line"
    show_markers = o.get("markers", "auto") == "on" or (
        o.get("markers", "auto") == "auto" and line and max(len(p) for p in drawn) <= 30)
    keys = []
    if len(series) > 1:
        for i, s in enumerate(series):
            flag = flag_of(s.get("flag"))
            color = flag_color(flag)
            keys.append((s["name"], line_key(i, color, show_markers) if line else point_key(i, color), flag))
    frame = Frame(spec, keys)
    frame.form = form
    frame.title_text = spec.title or f"{d['y']['label']} against {d['x']['label']}"
    xlab, ylab = axis_label(d["x"]), axis_label(d["y"])

    # Pass 1 and 2: margins from labels.
    left, bottom, right = 60.0, 50.0, 20.0
    for _ in range(3):
        x0, x1, y0, y1 = frame.plot_box(left, bottom, right)
        yt = _y_ticks(yk, ylo, yhi, y0, y1)
        new_left = 16 + 18 + y_label_room(yt)
        xt, xa = plan_x_continuous(xk, xlo, xhi, new_left, x1, o, nice=(xk != "time"))
        new_left = max(new_left, new_left + xa.left_overhang(new_left))
        new_bottom = xa.height() + 30
        new_right = max(20.0, xa.right_overhang(x1) + 8)
        if (new_left, new_bottom, new_right) == (left, bottom, right):
            break
        left, bottom, right = new_left, new_bottom, new_right
    x0, x1, y0, y1 = frame.plot_box(left, bottom, right)
    yt = _y_ticks(yk, ylo, yhi, y0, y1)
    xt, xa = plan_x_continuous(xk, xlo, xhi, x0, x1, o, nice=(xk != "time"))
    sx = Scale(xt.lo, xt.hi, x0, x1, log=(xk == "log"))
    sy = Scale(yt.lo, yt.hi, y1, y0, log=(yk == "log"))

    grid, yaxis = y_axis_svg(yt, sy, x0, x1, y0, y1, ylab, 18)
    xaxis = xa.svg(y1, x0, x1, xlab, frame.H - 12)
    groups = []
    for i, (s, pts) in enumerate(zip(series, drawn)):
        flag = flag_of(s.get("flag"))
        color = flag_color(flag)
        # Point flags index the source points; an aggregated series has other points, so they do not apply.
        warn, danger = (set(), set()) if agg else (set(s.get("warning", [])), set(s.get("danger", [])))
        cls = "series" + (f" {flag}" if flag else "")
        body = []
        if line:
            run = []
            runs = []
            for x, y in pts:
                ok = x is not None and y is not None and not (xk == "log" and x <= 0) and not (yk == "log" and y <= 0)
                if ok:
                    run.append((sx(x), sy(y)))
                elif run:
                    runs.append(run)
                    run = []
            if run:
                runs.append(run)
            dash = _style(i)[0]
            for r in runs:
                body.append(el("polyline", (("class", cls), ("points", " ".join(f"{num(a)},{num(b)}" for a, b in r)),
                                            ("fill", "none"), ("stroke", color), ("stroke-width", "2"),
                                            ("stroke-dasharray", dash or None), ("stroke-linejoin", "round"),
                                            ("stroke-linecap", "round"))))
            if show_markers:
                for j, (x, y) in enumerate(pts):
                    if x is None or y is None:
                        continue
                    pflag = "danger" if j in danger else "warn" if j in warn else None
                    body.append(marker(_style(i)[1], sx(x), sy(y), flag_color(pflag) if pflag else color, 3.5,
                                       cls="marker" + (f" {pflag}" if pflag else "")))
        else:
            for j, (x, y) in enumerate(pts):
                if x is None or y is None or (xk == "log" and x <= 0) or (yk == "log" and y <= 0):
                    continue
                pflag = "danger" if j in danger else "warn" if j in warn else None
                c = flag_color(pflag) if pflag else color
                g = scatter_glyph(i, sx(x), sy(y), c)
                body.append(el("g", (("class", pflag),), g) if pflag else g)
        groups.append(el("g", (("class", cls), ("data-name", s["name"])), "".join(body)))
    if len(series) > len(SERIES_STYLES):
        notes.append(f"{len(series)} series share {len(SERIES_STYLES)} styles: some look alike (small multiples "
                     "would separate them)")
    plot = el("g", (("class", "plot"),), "".join(groups))

    kind_words = "Line chart" if line else "Scatter plot"
    words = (f"{kind_words} of {ylab} against {xlab}: {len(series)} series "
             f"({', '.join(s['name'] for s in series[:8])}{', ...' if len(series) > 8 else ''}), "
             f"{sum(len(p) for p in drawn)} points. {d['x']['label']} runs from {value_words(xlo, xk)} to "
             f"{value_words(xhi, xk)}; {d['y']['label']} from {plain(min(ys))} to {plain(max(ys))}.")
    if xk == "log" or yk == "log":
        words += " " + " and ".join(f"the {a} axis" for a, k in (("x", xk), ("y", yk)) if k == "log") + \
                 " is logarithmic (base 10)."
    if agg:
        words += (f" Aggregated: each series is grouped into {agg['bins']} equal-width bins of "
                  f"{d['x']['label']}; {AGG_WORDS[agg['rule']]}.")
    body = frame.title_svg + frame.legend_svg + grid + yaxis + xaxis + plot
    return finish(frame, describe(spec, words), d, body), tuple(notes)


def make_line(spec):
    return _xy(spec, "chart.line")


def make_scatter(spec):
    return _xy(spec, "chart.scatter")


# --- bars -------------------------------------------------------------------------------------------------------

def make_bar(spec):
    d, o = spec.data, spec.options
    cats, series = d["categories"], d["series"]
    stacked = o.get("mode", "grouped") == "stacked"
    for s in series:
        if len(s["values"]) != len(cats):
            raise Fit(f"series {s['name']!r} has {len(s['values'])} values for {len(cats)} categories")
    notes = []
    if stacked:
        if any(v is not None and v < 0 for s in series for v in s["values"]):
            raise Fit("stacked bars need values of zero or more: use grouped bars for negative values")
        sums = [sum(s["values"][j] or 0 for s in series) for j in range(len(cats))]
        values = sums + [0.0]
    else:
        values = [v for s in series for v in s["values"] if v is not None]
    from_zero = bool(o.get("y_from_zero", True))
    ylo, yhi, n2 = _y_domain(values, o, from_zero=from_zero)
    notes += n2
    sid = scope_id("chart.bar", spec.to_json())
    keys = []
    if len(series) > 1:
        for i, s in enumerate(series):
            flag = flag_of(s.get("flag"))
            keys.append((s["name"], bar_key(sid, i, flag_color(flag)), flag))
    frame = Frame(spec, keys)
    frame.form = "chart.bar"
    frame.title_text = spec.title or f"{d['y']['label']} by {d['x']['label']}"
    xlab, ylab = axis_label(d["x"]), axis_label(d["y"])
    left, bottom = 60.0, 50.0
    for _ in range(3):
        x0, x1, y0, y1 = frame.plot_box(left, bottom)
        yt = _y_ticks("linear", ylo, yhi, y0, y1)
        new_left = 16 + 18 + y_label_room(yt)
        xa = plan_x_band(cats, new_left, x1, o)
        new_left = max(new_left, new_left + xa.left_overhang(new_left))
        new_bottom = xa.height() + 30
        if (new_left, new_bottom) == (left, bottom):
            break
        left, bottom = new_left, new_bottom
    x0, x1, y0, y1 = frame.plot_box(left, bottom)
    yt = _y_ticks("linear", ylo, yhi, y0, y1)
    xa = plan_x_band(cats, x0, x1, o)
    sy = Scale(yt.lo, yt.hi, y1, y0)
    truncated = yt.lo > 0 or yt.hi < 0
    if truncated:
        notes.append("the value axis does not start at zero: bar lengths are not proportional to the values")
    centres, bw = band(len(cats), x0, x1)
    grid, yaxis = y_axis_svg(yt, sy, x0, x1, y0, y1, ylab, 18)
    xaxis = xa.svg(y1, x0, x1, xlab, frame.H - 12, domain=False)
    base_v = 0.0 if yt.lo <= 0 <= yt.hi else yt.lo
    groups = []
    n = len(series)
    sub = bw / n if not stacked else bw
    running = [0.0] * len(cats)
    for i, s in enumerate(series):
        flag = flag_of(s.get("flag"))
        color = flag_color(flag)
        warn, danger = set(s.get("warning", [])), set(s.get("danger", []))
        fill, opacity = bar_fill(sid, _style(i)[2])
        rects = []
        for j, v in enumerate(s["values"]):
            if v is None:
                continue
            pflag = "danger" if j in danger else "warn" if j in warn else None
            c = flag_color(pflag) if pflag else color
            if stacked:
                a, b = running[j], running[j] + v
                running[j] = b
                x = centres[j] - bw / 2
            else:
                a, b = base_v, v
                x = centres[j] - bw / 2 + i * sub
            ya, yb = sy(a), sy(b)
            top, h = min(ya, yb), abs(ya - yb)
            rects.append(el("rect", (("class", "mark" + (f" {pflag}" if pflag else "")), ("x", num(x)), ("y", num(top)),
                                     ("width", num(sub)), ("height", num(h)), ("fill", fill), ("fill-opacity", opacity),
                                     ("stroke", c), ("stroke-width", "2" if pflag else "1"))))
        groups.append(el("g", (("class", "series" + (f" {flag}" if flag else "")), ("data-name", s["name"])),
                         "".join(rects)))
    plot = el("g", (("class", "plot"),), "".join(groups))
    allv = [v for s in series for v in s["values"] if v is not None]
    words = (f"{'Stacked' if stacked else 'Grouped' if n > 1 else 'Bar'} bar chart of {ylab} for {len(cats)} "
             f"categories of {d['x']['label']}" + (f", {n} series ({', '.join(s['name'] for s in series[:8])})"
                                                    if n > 1 else "")
             + (f". Values range from {plain(min(allv))} to {plain(max(allv))}." if allv else "."))
    if stacked:
        words += " Each bar's segments are stacked from zero in series order."
    if truncated:
        words += (f" The value axis runs from {plain(yt.lo)} to {plain(yt.hi)}, not from zero: bar lengths are not "
                  "proportional to the values.")
    if n > len(SERIES_STYLES):
        notes.append(f"{n} series share {len(SERIES_STYLES)} fill styles: some look alike")
    body = frame.title_svg + frame.legend_svg + grid + yaxis + xaxis + plot
    return finish(frame, describe(spec, words), d, body, defs=patterns(frame.sid)), tuple(notes)


# --- histogram --------------------------------------------------------------------------------------------------

def histogram_edges(values, opts):
    lo, hi = min(values), max(values)
    if "bin_width" in opts and opts["bin_width"] > 0:
        w = float(opts["bin_width"])
        # bins [k*w, (k+1)*w) from the one that holds the smallest value to the one that holds the largest: a value
        # on an inner edge belongs to the upper bin, so the largest value never sits on the right edge (17.5 with a
        # width of 2.5 is in the bin 17.5-20, not the last edge of 15-17.5); the quotient is rounded to 9 places so
        # that 0.3 / 0.1 counts as 3
        k0, k1 = math.floor(round(lo / w, 9)), math.floor(round(hi / w, 9)) + 1
        if k1 - k0 > 500:
            raise Fit("the bin width gives more than 500 bins")
        places = decimals_of(w)
        return [float(f"{k * w:.{places}f}") for k in range(k0, k1 + 1)]
    # Sturges' rule (ceil(log2 n) + 1 bins, 5 to 50) unless the spec names a count; edges on nice numbers.
    target = int(opts.get("bins", min(50, max(5, math.ceil(math.log2(len(values))) + 1))))
    return list(linear_ticks(lo, hi, max(2, round(target * 1.5))).values)   # nice steps at or above the count


def make_histogram(spec):
    d, o = spec.data, spec.options
    values = [float(v) for v in d["values"]]
    edges = histogram_edges(values, o)
    counts = [0] * (len(edges) - 1)
    for v in values:   # [edge_i, edge_i+1), the last bin closed
        counts[min(len(counts) - 1, bisect.bisect_right(edges, v) - 1)] += 1
    frame = Frame(spec, [])
    frame.form = "chart.histogram"
    frame.title_text = spec.title or f"Distribution of {d['x']['label']}"
    ydef = d.get("y") or {"label": "Count", "unit": None}
    xlab, ylab = axis_label(d["x"]), axis_label(ydef)
    left, bottom = 60.0, 50.0
    for _ in range(3):
        x0, x1, y0, y1 = frame.plot_box(left, bottom)
        yt = _y_ticks("linear", 0, max(counts), y0, y1)
        new_left = 16 + 18 + y_label_room(yt)
        _, xa = plan_x_continuous("linear", edges[0], edges[-1], new_left, x1, o)
        new_left = max(new_left, new_left + xa.left_overhang(new_left))
        new_bottom = xa.height() + 30
        if (new_left, new_bottom) == (left, bottom):
            break
        left, bottom = new_left, new_bottom
    x0, x1, y0, y1 = frame.plot_box(left, bottom)
    yt = _y_ticks("linear", 0, max(counts), y0, y1)
    xt, xa = plan_x_continuous("linear", edges[0], edges[-1], x0, x1, o)
    sx = Scale(xt.lo, xt.hi, x0, x1)
    sy = Scale(yt.lo, yt.hi, y1, y0)
    grid, yaxis = y_axis_svg(yt, sy, x0, x1, y0, y1, ylab, 18)
    xaxis = xa.svg(y1, x0, x1, xlab, frame.H - 12)
    rects = []
    for i, c in enumerate(counts):
        if c == 0:
            continue
        a, b = sx(edges[i]), sx(edges[i + 1])
        rects.append(el("rect", (("class", "mark"), ("x", num(a)), ("y", num(sy(c))), ("width", num(b - a)),
                                 ("height", num(sy(0) - sy(c))), ("fill", "currentColor"), ("fill-opacity", "0.72"),
                                 ("stroke", "var(--nsf-paper)"), ("stroke-width", "1"))))
    name = d.get("name") or d["x"]["label"]
    plot = el("g", (("class", "plot"),), el("g", (("class", "series"), ("data-name", name)), "".join(rects)))
    words = (f"Histogram of {len(values)} values of {xlab} in {len(counts)} bins of width "
             f"{plain(edges[1] - edges[0])} from {plain(edges[0])} to {plain(edges[-1])}; a value on an inner edge "
             f"belongs to the upper bin, and the last bin is closed on the right. "
             f"The fullest bin holds {max(counts)} values.")
    body = frame.title_svg + grid + yaxis + xaxis + plot
    return finish(frame, describe(spec, words), d, body), ()


# --- heatmap ----------------------------------------------------------------------------------------------------

RAMP_LIGHT, RAMP_DARK = 242, 38     # gray level of the lowest and of the highest value


def make_heatmap(spec):
    d, o = spec.data, spec.options
    xc, yc, grid_v = d["x"]["categories"], d["y"]["categories"], d["values"]
    if len(grid_v) != len(yc) or any(len(r) != len(xc) for r in grid_v):
        raise Fit("values must have one row per y category and one column per x category")
    flat = [v for r in grid_v for v in r if v is not None]
    if not flat:
        raise Fit("there are no values to draw")
    zt = linear_ticks(min(flat), max(flat), 5)
    frame = Frame(spec, [])
    frame.form = "chart.heatmap"
    frame.title_text = spec.title or f"{d['z']['label']} by {d['x']['label']} and {d['y']['label']}"
    xlab, ylab, zlab = axis_label(d["x"]), axis_label(d["y"]), axis_label(d["z"])
    ylabels = [(c, False) for c in yc]          # full row labels: a reader reads the row from its tick's text
    left = 16 + 18 + max(text_width(s, TICK_SIZE) for s, _ in ylabels) + 12
    right = 24 + max(20 + max(text_width(t, TICK_SIZE) for t in zt.labels), text_width(zlab, LABEL_SIZE)) + 16
    bottom = 50.0
    for _ in range(3):
        x0, x1, y0, y1 = frame.plot_box(left, bottom, right)
        xa = plan_x_band(xc, x0, x1, {**o, "x_labels": o.get("x_labels", "auto")})
        nb = xa.height() + 30
        if nb == bottom:
            break
        bottom = nb
    x0, x1, y0, y1 = frame.plot_box(left, bottom, right)
    xa = plan_x_band(xc, x0, x1, o)
    cw, ch = (x1 - x0) / len(xc), (y1 - y0) / len(yc)

    def level(v):
        t = 0.0 if zt.hi == zt.lo else (v - zt.lo) / (zt.hi - zt.lo)
        return round(RAMP_LIGHT + (RAMP_DARK - RAMP_LIGHT) * t)

    cells, texts = [], []
    show = o.get("print_values", "auto")
    labels = [[plain(v, 4) if v is not None else "" for v in r] for r in grid_v]
    fits = all(text_width(t, TICK_SIZE) + 6 <= cw for r in labels for t in r) and ch >= TICK_SIZE + 4
    printed = show == "on" or (show == "auto" and len(flat) <= 400 and fits)
    rows = []
    for r, row in enumerate(grid_v):
        cells = []
        for c, v in enumerate(row):
            x, y = x0 + c * cw, y0 + r * ch
            if v is None:
                cells.append(el("rect", (("class", "missing"), ("x", num(x)), ("y", num(y)), ("width", num(cw)),
                                         ("height", num(ch)), ("fill", f"url(#{frame.sid}-hatch)"),
                                         ("stroke", "var(--nsf-paper)"))))
                continue
            g = level(v)
            cells.append(el("rect", (("class", "mark"), ("x", num(x)), ("y", num(y)), ("width", num(cw)),
                                     ("height", num(ch)), ("fill", gray_hex(g)), ("stroke", "var(--nsf-paper)"),
                                     ("stroke-width", "1"))))
            if printed:
                ink = "#000000" if contrast((g, g, g), (0, 0, 0)) >= contrast((g, g, g), (255, 255, 255)) else "#ffffff"
                texts.append(text_el(x + cw / 2, y + ch / 2 + TICK_SIZE * 0.35, labels[r][c], size=TICK_SIZE,
                                     anchor="middle", cls="value", fill=ink))
        rows.append(el("g", (("class", "series"), ("data-name", yc[r])), "".join(cells)))
    plot = el("g", (("class", "plot"),), "".join(rows) + el("g", (("class", "values"),), "".join(texts)))
    # y categories axis
    yt = []
    for r, (shown, cut) in enumerate(ylabels):
        yy = y0 + (r + 0.5) * ch
        yt.append(el("g", (("class", "tick"), ("transform", f"translate({num(x0)},{num(yy)})")),
                     text_el(-8, TICK_SIZE * 0.35, shown, size=TICK_SIZE, anchor="end", opacity=MUTED,
                             full=yc[r] if cut else None)))
    yaxis = el("g", (("class", "axis y"),), "".join(yt) + text_el(18, (y0 + y1) / 2, ylab, size=LABEL_SIZE,
                                                                   anchor="middle", cls="label", opacity=MUTED,
                                                                   rotate=-90))
    xaxis = xa.svg(y1, x0, x1, xlab, frame.H - 12, domain=False)
    # z ramp: swatches with their values
    zx = x1 + 24
    zs = Scale(zt.lo, zt.hi, y1, y0 + 14)
    zticks = []
    for v, t in zip(zt.values, zt.labels):
        zticks.append(el("g", (("class", "tick"), ("transform", f"translate({num(zx)},{num(zs(v))})")),
                         el("rect", (("class", "swatch"), ("x", "0"), ("y", "-6"), ("width", "14"), ("height", "12"),
                                     ("fill", gray_hex(level(v))), ("stroke", "currentColor"),
                                     ("stroke-opacity", num(AXIS))))
                         + text_el(20, TICK_SIZE * 0.35, t, size=TICK_SIZE, opacity=MUTED)))
    zaxis = el("g", (("class", "axis z"),), "".join(zticks) + text_el(zx, y0, zlab, size=LABEL_SIZE,
                                                                         cls="label", opacity=MUTED))
    words = (f"Heatmap of {zlab} for {len(yc)} rows of {d['y']['label']} by {len(xc)} columns of "
             f"{d['x']['label']}. Darker cells hold larger values: gray level runs linearly from light at "
             f"{plain(zt.lo)} to dark at {plain(zt.hi)}; values range from {plain(min(flat))} to {plain(max(flat))}."
             + (" Each cell prints its value." if printed else "")
             + (" Hatched cells have no value." if len(flat) < len(xc) * len(yc) else ""))
    body = frame.title_svg + yaxis + xaxis + plot + zaxis
    return finish(frame, describe(spec, words), d, body, defs=patterns(frame.sid)), ()


# --- small multiples --------------------------------------------------------------------------------------------

def make_small_multiples(spec):
    """One small line chart per series, all on the same x and y domains, in a grid."""
    d, o = spec.data, spec.options
    series = d["series"]
    xk, yk = d["x"].get("scale", "linear"), d["y"].get("scale", "linear")
    n = len(series)
    cols = int(o.get("columns", min(n, 3 if n > 4 else 2 if n > 1 else 1)))
    rows = math.ceil(n / cols)
    W = float(o.get("width", 720))
    H = float(o.get("height", 64 + rows * 190))
    pts_all = [(p[0], p[1]) for s in series for p in s["points"] if p[0] is not None and p[1] is not None]
    if not pts_all:
        raise Fit("there are no complete points to draw")
    xlo, xhi = min(p[0] for p in pts_all), max(p[0] for p in pts_all)
    ylo, yhi, notes = _y_domain([p[1] for p in pts_all], o, from_zero=bool(o.get("y_from_zero", False)),
                                log=(yk == "log"))
    sid = scope_id("chart.small_multiples", spec.to_json())
    title_text = spec.title or f"{d['y']['label']} against {d['x']['label']}, one panel per series"
    title_svg, top = title_block(W, title_text)
    pw, ph = (W - 16) / cols, (H - top - 30) / rows
    panels = []
    xlab, ylab = axis_label(d["x"]), axis_label(d["y"])
    for k, s in enumerate(series):
        r, c = divmod(k, cols)
        px, py = 8 + c * pw, top + r * ph
        yt = log_ticks(ylo, yhi, 3) if yk == "log" else linear_ticks(ylo, yhi, max(2, round((ph - 70) / 40)))
        left = px + 8 + y_label_room(yt)
        x0, x1, y0, y1 = left, px + pw - 14, py + 28, py + ph - 40
        if x1 - x0 < 60 or y1 - y0 < 40:
            raise Fit("the panels are too small: make the picture larger or use more columns")
        xt, xa = plan_x_continuous(xk, xlo, xhi, x0, x1, o, nice=(xk != "time"), per_tick=60)
        sx = Scale(xt.lo, xt.hi, x0, x1, log=(xk == "log"))
        sy = Scale(yt.lo, yt.hi, y1, y0, log=(yk == "log"))
        grid, yaxis = y_axis_svg(yt, sy, x0, x1, y0, y1, "", px + 4)
        xaxis = xa.svg(y1, x0, x1, "", y1 + 34)
        flag = flag_of(s.get("flag"))
        color = flag_color(flag)
        run, runs = [], []
        for x, y in ((p[0], p[1]) for p in s["points"]):
            if x is None or y is None or (xk == "log" and x <= 0) or (yk == "log" and y <= 0):
                if run:
                    runs.append(run)
                run = [] if x is None or y is None else run
                continue
            run.append((sx(x), sy(y)))
        if run:
            runs.append(run)
        lines = "".join(el("polyline", (("class", "series" + (f" {flag}" if flag else "")),
                                        ("points", " ".join(f"{num(a)},{num(b)}" for a, b in rr)), ("fill", "none"),
                                        ("stroke", color), ("stroke-width", "1.75"), ("stroke-linejoin", "round"))) for rr in runs)
        name, cut = abbreviate(s["name"], pw - 24, 13)
        panels.append(el("g", (("class", "panel"), ("data-name", s["name"])),
                         text_el(px + 8, py + 16, name, size=13, weight="600", cls="panel-title",
                                 full=s["name"] if cut else None)
                         + grid + yaxis + xaxis
                         + el("g", (("class", "plot"),),
                              el("g", (("class", "series" + (f" {flag}" if flag else "")), ("data-name", s["name"])),
                                 lines))))
    shared = (text_el(W / 2, H - 10, f"{xlab} (all panels)", size=LABEL_SIZE, anchor="middle", cls="label",
                      opacity=MUTED)
              + text_el(W - 16, 28, f"vertical: {ylab}, same scale in every panel", size=LABEL_SIZE, anchor="end",
                        cls="label", opacity=MUTED))
    words = (f"Small multiples: {n} panels, one per series of {ylab} against {xlab}, in {rows} rows of up to {cols}; "
             f"every panel uses the same scales. {d['x']['label']} runs from {value_words(xlo, xk)} to "
             f"{value_words(xhi, xk)}; {d['y']['label']} from {plain(ylo)} to {plain(yhi)}.")
    if text_width(title_text, TITLE_SIZE) + text_width(shared, LABEL_SIZE) > W:
        shared = text_el(W / 2, H - 10, f"{xlab} (all panels); vertical: {ylab}", size=LABEL_SIZE, anchor="middle",
                         cls="label", opacity=MUTED)
    body = title_svg + shared + "".join(panels)
    desc = describe(spec, words)
    meta = d if len(str(d)) <= METADATA_MAX else None
    return head(sid, "chart.small_multiples", W, H, title_text, desc, meta) + body + "</svg>", tuple(notes)


GENERATORS = {
    "chart.line": (make_line, XY_DATA),
    "chart.scatter": (make_scatter, XY_DATA),
    "chart.bar": (make_bar, BAR_DATA),
    "chart.histogram": (make_histogram, HIST_DATA),
    "chart.heatmap": (make_heatmap, HEAT_DATA),
    "chart.small_multiples": (make_small_multiples, XY_DATA),
}

__all__ = ["GENERATORS", "OPTIONS", "aggregate", "axis_label", "plain"]
