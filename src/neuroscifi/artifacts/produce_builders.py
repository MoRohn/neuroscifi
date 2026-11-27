"""Specification builders (OMNIMIND Wave O-1, lane P, ADR-076): a typed representation + the request + the plan's form
-> the specification of that form, WITHOUT inventing anything.

A builder is `fn(pieces, request_text, options) -> Built`, or it raises `CannotBuild` with the reason in plain words.
`pieces` are what the production read from its sources (`Piece`: the source's id and label, the decoded
representation, its shape). Rules every builder keeps:
- Every value in a spec comes from the representation: column values, sample measurements, node names, headings.
  Titles, axis labels and units come from the content (column names, declared units, the file's label), never from
  imagination; where the content has no name for something, the builder says what it is in fixed words ("row number",
  "seconds", "frequency (Hz)").
- What a form cannot show is left out by a stated rule, and the rule is written into the spec's description and into
  `Built.rules` (which the production record keeps): "the first 2000 of 5000 rows", "the 6 series with the widest
  spread of 9".
- A column named in the request is chosen: names are matched in the request's words, whole words, case and
  underscores ignored; "A against B", "A versus B", "A by B" and "A over B" name the y and the x of a chart.
- Every spec says, in its description, that it was made by the system from the named sources and is not evidence.
Nothing here reads bytes or calls a model; nothing here makes or checks anything (produce.py does, through `make`
and `check`).
"""
from __future__ import annotations

import math
import re
from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np

from .types import ArtifactError, ContentShape

MADE = "Made by the system from {sources}; not evidence."
MAX_SERIES_LINES = 6           # line styles a line chart tells apart (forms.LINE_STYLES)
MAX_PANELS = 24                # small multiples (forms.MAX_PANELS)
MAX_BAR_SERIES = 4             # bar patterns (forms.BAR_PATTERNS)
MAX_BARS = 60                  # categories one bar chart shows
MAX_TABLE_ROWS = 2000          # rows an HTML table shows
MAX_TABLE_COLUMNS = 50
MAX_EVENTS = 500               # events a timeline shows (forms: "every dated event up to 500")
MAX_NODES = 150                # nodes a node-link diagram shows (forms: "about 150 nodes")
MAX_TREE_NODES = 300           # nodes a tree diagram shows
MAX_SIBLINGS = 24              # children of one node a tree diagram shows side by side (fix lane F3: 5000 files)
ENVELOPE_WINDOWS = 480         # windows of a sound's amplitude envelope
SPECTRUM_FFT = 4096            # samples per spectrum window (Hann, half overlap)
SPECTRUM_MAX_WINDOWS = 512     # windows averaged at most (evenly spread over the sound)
RASTER_SIDE = 256              # the longest side of a derived image view, in cells
MAX_HIST_BINS, MIN_HIST_BINS = 40, 5
MAX_SOLID_VIEW_VERTICES, MAX_SOLID_VIEW_FACES = 20_000, 40_000
LINE_WINDOWS = 2000            # a longer series is drawn as the extremes of this many windows of rows (fix lane F3)
MAX_SCATTER_POINTS = 5000      # a scatter of more rows draws every k-th row


class CannotBuild(ArtifactError):
    """No specification of this form can be built from this content without inventing something; `str()` says why."""

    reason = "unsupported"


@dataclass(frozen=True)
class Piece:
    source: str                 # the artifact id it came from
    label: str                  # the source's label (the owner's file name)
    representation: object
    format: str
    shape: ContentShape
    reading: str = "as decoded"  # how this piece was read from the source
    part: str = ""              # when only part of the source was read (the owner agreed, --partial): what part
    reduced: str = ""           # when a large source was reduced to be made from (produce.reduce_for_making): how
    decoded_as: tuple = ()      # lane FX5 (C6a): (format, the decoder's notes, partial) for the relater; () unknown


@dataclass(frozen=True)
class Built:
    spec: dict
    used: dict = field(default_factory=dict)        # which parts of the content became which part of the form
    rules: tuple[str, ...] = ()                     # what was left out and by which rule, in plain words


# ---------------------------------------------------------------------------------------------------------------
# Small helpers


def _cut(text, n=200) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= n else text[:n - 1] + "…"


def _sources(pieces) -> str:
    labels = list(dict.fromkeys(p.label for p in pieces))
    return ", ".join(labels[:6]) + (f" and {len(labels) - 6} more" if len(labels) > 6 else "")


def _spec(form: str, title: str, description: str, data: dict, pieces, options: dict | None = None,
          rules=()) -> dict:
    words = " ".join([description.strip()] + [r[:1].upper() + r[1:] + "." for r in rules] +
                     [f"Only part of {p.label} was read: {p.part}." for p in pieces if p.part] +
                     [f"{p.label} was {p.reduced}." for p in pieces if p.reduced] +
                     [MADE.format(sources=_sources(pieces))])
    return {"spec": form, "version": 1, "title": _cut(title, 200), "description": _cut(words, 2000), "data": data,
            "options": dict(options or {}), "sources": list(dict.fromkeys(p.source for p in pieces)),
            "made_by": {"generator": "", "version": 0}}


def _num(v):
    """A JSON number or None for a missing value (NaN)."""
    if v is None:
        return None
    f = float(v)
    if not math.isfinite(f):
        return None
    return int(f) if f.is_integer() and abs(f) < 2 ** 53 else f


def _norm(text: str) -> str:
    return " ".join(re.sub(r"[_\-]+", " ", str(text).lower()).split())


def named_in(request: str, names) -> list[str]:
    """The names that the request's words name, in the order they appear (whole words; case, underscores and hyphens
    ignored; a longer name is matched before a name it contains)."""
    words = " " + " ".join(re.sub(r"[^\w\s]", " ", _norm(request)).split()) + " "
    found = []
    taken = [False] * len(words)
    for name in sorted(dict.fromkeys(names), key=lambda n: -len(_norm(n))):
        key = " ".join(re.sub(r"[^\w\s]", " ", _norm(name)).split())
        if not key:
            continue
        at = words.find(" " + key + " ")
        while at >= 0 and any(taken[at + 1:at + 1 + len(key)]):
            at = words.find(" " + key + " ", at + 1)
        if at >= 0:
            for i in range(at + 1, at + 1 + len(key)):
                taken[i] = True
            found.append((at, name))
    return [name for _, name in sorted(found)]


_AGAINST = re.compile(r"\b(?:against|versus|vs|by|over|per)\b")


def axes_named(request: str, names) -> tuple[str | None, list[str]]:
    """(x, ys) named by the request: "A against/versus/by/over B" makes B the x; otherwise no x is named and the
    named columns are ys."""
    named = named_in(request, names)
    if len(named) < 2:
        return None, named
    words = " " + " ".join(re.sub(r"[^\w\s]", " ", _norm(request)).split()) + " "
    last = named[-1]
    key = " " + " ".join(re.sub(r"[^\w\s]", " ", _norm(last)).split()) + " "
    pos = words.rfind(key)
    between = words[:pos]
    first_key = " " + " ".join(re.sub(r"[^\w\s]", " ", _norm(named[-2])).split()) + " "
    between = between[between.rfind(first_key) + len(first_key) - 1:]
    if _AGAINST.search(between):
        return last, named[:-1]
    return None, named


def _table(piece):
    rep = piece.representation
    if getattr(rep, "kind", None) != "table":
        raise CannotBuild(f"{piece.label} is not a table")
    if not rep.columns or not rep.n_rows:
        raise CannotBuild(f"{piece.label} has no rows to show")
    return rep


def _values(column) -> list:
    if column.kind == "text":
        return [str(v) for v in column.values]
    if column.kind == "boolean":
        return [None if (isinstance(v, float) and not math.isfinite(v)) else bool(v) for v in column.values]
    return [_num(v) for v in np.asarray(column.values, dtype=np.float64)]


def _numeric(table):
    return [c for c in table.columns if c.kind in ("number", "integer")]


def _by_name(table, name):
    return next(c for c in table.columns if c.name == name)


def _axis(column, *, scale=None) -> dict:
    axis = {"label": _cut(column.name, 200)}
    if column.unit:
        axis["unit"] = _cut(column.unit, 40)
    if scale:
        axis["scale"] = scale
    return axis


def _monotonic(column) -> bool:
    v = np.asarray(column.values, dtype=np.float64)
    v = v[np.isfinite(v)]
    return v.size >= 2 and bool(np.all(np.diff(v) > 0))


def _one(pieces, what="one source"):
    if len(pieces) != 1:
        raise CannotBuild(f"this form is made from {what}; {len(pieces)} were given")
    return pieces[0]


def _spread(column) -> float:
    v = np.asarray(column.values, dtype=np.float64)
    v = v[np.isfinite(v)]
    return float(v.max() - v.min()) if v.size else 0.0


# ---------------------------------------------------------------------------------------------------------------
# Tables -> charts


def _magnitude(column) -> float:
    v = np.abs(np.asarray(column.values, dtype=np.float64))
    v = v[np.isfinite(v)]
    return float(v.max()) if v.size else 0.0


def one_scale(columns) -> tuple[list, str | None]:
    """The columns that can share one value axis: those whose largest absolute value is at least a tenth of the
    largest of them all (a column ten times smaller would lie flat along the axis). Returns (kept, the rule in words
    when something was left out)."""
    if len(columns) < 2:
        return list(columns), None
    top = max(_magnitude(c) for c in columns)
    kept = [c for c in columns if top == 0 or _magnitude(c) >= top / 10]
    out = [c for c in columns if c not in kept]
    if not out:
        return kept, None
    return kept, (f"{', '.join(c.name for c in out)} left out: {'its' if len(out) == 1 else 'their'} largest "
                  f"value{'' if len(out) == 1 else 's'} ({', '.join(f'{_magnitude(c):g}' for c in out)}) "
                  f"{'is' if len(out) == 1 else 'are'} more than ten times smaller than "
                  f"{max(kept, key=_magnitude).name}'s ({top:g}), so {'it' if len(out) == 1 else 'they'} would lie "
                  "flat on one axis (name a column in the request to draw it)")


def _x_and_series(table, request: str, *, many: int):
    """Choose the x column and the y series of an x-y chart. Returns (x column or None for row numbers, ys, rules)."""
    names = [c.name for c in table.columns]
    named_x, named = axes_named(request, names)
    rules = []
    numeric = _numeric(table)
    if named_x is not None:
        x = _by_name(table, named_x)
        if x.kind not in ("time", "number", "integer"):
            raise CannotBuild(f"the column {named_x!r} named for the x axis holds {x.kind}, not numbers or times")
    else:
        times = [c for c in table.columns if c.kind == "time"]
        rising = [c for c in numeric if _monotonic(c)]
        x = times[0] if times else (rising[0] if rising and len(numeric) > 1 else None)
    ys = [_by_name(table, n) for n in named if n != getattr(x, "name", None)]
    bad = [c.name for c in ys if c.kind not in ("number", "integer")]
    if bad:
        raise CannotBuild(f"the column {bad[0]!r} named in the request holds {_by_name(table, bad[0]).kind}, not "
                          "numbers")
    if not ys:
        ys = [c for c in numeric if c is not x]
        if not ys:
            raise CannotBuild("the table has no column of numbers to draw")
        ys, rule = one_scale(ys)
        if rule:
            rules.append(rule)
    if len(ys) > many:
        keep = sorted(ys, key=lambda c: (-_spread(c), c.name))[:many]
        rules.append(f"the {many} series with the widest spread (largest minus smallest value) of {len(ys)} are "
                     f"shown: {', '.join(c.name for c in ys if c not in keep)} left out")
        ys = [c for c in ys if c in keep]
    if x is None:
        rules.append("no column of times or of rising numbers was found, so the x axis is the row number "
                     "(1 is the file's first row)")
    return x, ys, rules


def _points(table, x, y) -> list:
    xs = [_num(v) for v in np.asarray(x.values, dtype=np.float64)] if x is not None else list(range(1, table.n_rows + 1))
    ys = [_num(v) for v in np.asarray(y.values, dtype=np.float64)]
    return [[a, b] for a, b in zip(xs, ys, strict=True) if a is not None]


def _envelope_points(points: list) -> list:
    """At most 2 x LINE_WINDOWS points: in each of LINE_WINDOWS equal windows of rows, the row with the smallest and
    the row with the largest value, in row order (every extreme of the series is kept; gaps are dropped)."""
    pts = [p for p in points if p[1] is not None]
    n = len(pts)
    if n <= 2 * LINE_WINDOWS:
        return pts
    out = []
    for w in range(LINE_WINDOWS):
        window = pts[w * n // LINE_WINDOWS:(w + 1) * n // LINE_WINDOWS]
        if not window:
            continue
        lo = min(range(len(window)), key=lambda i: window[i][1])
        hi = max(range(len(window)), key=lambda i: window[i][1])
        out.extend(window[i] for i in sorted({lo, hi}))
    return out


def _y_axis(ys) -> dict:
    units = {c.unit for c in ys}
    axis = {"label": _cut(ys[0].name if len(ys) == 1 else ", ".join(c.name for c in ys), 200)}
    if len(units) == 1 and ys[0].unit:
        axis["unit"] = _cut(ys[0].unit, 40)
    return axis


def build_line(pieces, request, options, *, small_multiples=False) -> Built:
    piece = _one(pieces)
    if getattr(piece.representation, "kind", None) == "signal":
        return _envelope_chart(piece, options)
    table = _table(piece)
    x, ys, rules = _x_and_series(table, request, many=MAX_PANELS if small_multiples else MAX_SERIES_LINES)
    form = "chart.line"
    if not small_multiples and len(ys) > MAX_SERIES_LINES:
        form = "chart.small_multiples"
    if small_multiples:
        form = "chart.small_multiples"
    x_axis = _axis(x, scale="time" if x.kind == "time" else "linear") if x is not None else \
        {"label": "row number", "scale": "linear"}
    series = [{"name": _cut(c.name, 200), "points": _points(table, x, c)} for c in ys]
    longest = max((len(s["points"]) for s in series), default=0)
    if longest > 2 * LINE_WINDOWS:
        series = [s | {"points": _envelope_points(s["points"])} for s in series]
        rules.append(f"the {longest} rows are drawn as the smallest and the largest value in each of {LINE_WINDOWS} "
                     "equal windows of rows (every extreme is kept; the values between them are not drawn)")
    xname = x.name if x is not None else "row number"
    kind = "small multiples (one panel per series)" if form == "chart.small_multiples" else "a line chart"
    desc = (f"{kind[:1].upper() + kind[1:]} of {', '.join(c.name for c in ys)} against {xname}, {table.n_rows} rows "
            f"of {piece.label}.")
    used = {"x": xname, "y": [c.name for c in ys], "rows": table.n_rows}
    return Built(_spec(form, f"{', '.join(c.name for c in ys)} by {xname}", desc,
                       {"x": x_axis, "y": _y_axis(ys), "series": series}, pieces, rules=rules),
                 used, tuple(rules))


def build_multiples(pieces, request, options) -> Built:
    return build_line(pieces, request, options, small_multiples=True)


def build_scatter(pieces, request, options) -> Built:
    piece = _one(pieces)
    rep = piece.representation
    if getattr(rep, "kind", None) == "features":
        return _coordinates(piece)
    table = _table(piece)
    names = [c.name for c in table.columns]
    named_x, named = axes_named(request, names)
    numeric = _numeric(table)
    if named_x is not None and named:
        x, y = _by_name(table, named_x), _by_name(table, named[0])
    elif len(named) >= 2:
        x, y = _by_name(table, named[0]), _by_name(table, named[1])
    else:
        rest = [c for c in numeric if c.name not in named]
        chosen = [_by_name(table, n) for n in named] + rest
        if len(chosen) < 2:
            raise CannotBuild("a scatter chart needs two columns of numbers")
        x, y = chosen[0], chosen[1]
    for c in (x, y):
        if c.kind not in ("number", "integer", "time"):
            raise CannotBuild(f"the column {c.name!r} holds {c.kind}, not numbers")
    pts = [p for p in _points(table, x, y) if p[1] is not None]
    rules = []
    if len(pts) > MAX_SCATTER_POINTS:
        step = -(-len(pts) // MAX_SCATTER_POINTS)
        rules.append(f"one row in every {step} of the {len(pts)} rows is drawn ({len(pts[::step])} points), "
                     "starting with the first")
        pts = pts[::step]
    if len(numeric) > 2:
        rules.append(f"two of the {len(numeric)} numeric columns are drawn ({x.name} across, {y.name} up)")
    desc = f"A scatter chart of {y.name} against {x.name}: {len(pts)} points, one per row of {piece.label}."
    return Built(_spec("chart.scatter", f"{y.name} against {x.name}", desc,
                       {"x": _axis(x, scale="time" if x.kind == "time" else "linear"), "y": _axis(y),
                        "series": [{"name": _cut(y.name, 200), "points": pts}]}, pieces, rules=rules),
                 {"x": x.name, "y": [y.name], "points": len(pts)}, tuple(rules))


def _coordinates(piece) -> Built:
    rep = piece.representation
    pts = []
    for f in rep.features:
        for row in np.asarray(f.coordinates, dtype=np.float64)[:, :2]:
            pts.append([_num(row[0]), _num(row[1])])
    if len(pts) < 1:
        raise CannotBuild(f"{piece.label} holds no coordinates")
    crs = rep.crs or "the file's coordinates"
    rules = ["the two axes are scaled separately, so distances across and up are not to the same scale (no map form "
             "exists here yet)"]
    desc = (f"The {len(pts)} coordinates of the {len(rep.features)} features of {piece.label}, as points "
            f"(first coordinate across, second up; {crs}).")
    data = {"x": {"label": "first coordinate (x or longitude)"}, "y": {"label": "second coordinate (y or latitude)"},
            "series": [{"name": "coordinates", "points": pts[:100_000]}]}
    if len(pts) > 100_000:
        rules.append(f"the first 100000 of {len(pts)} coordinates are drawn")
    return Built(_spec("chart.scatter", f"Places in {piece.label}", desc, data, [piece], rules=rules),
                 {"points": min(len(pts), 100_000), "features": len(rep.features)}, tuple(rules))


def build_bar(pieces, request, options) -> Built:
    table = _table(_one(pieces))
    piece = pieces[0]
    names = [c.name for c in table.columns]
    named = named_in(request, names)
    texts = [c for c in table.columns if c.kind == "text"]
    cat = next((_by_name(table, n) for n in named if _by_name(table, n).kind == "text"), texts[0] if texts else None)
    if cat is None:
        raise CannotBuild("a bar chart needs a column of names (categories); this table has none")
    labels = [str(v) for v in cat.values]
    if len(set(labels)) != len(labels):
        raise CannotBuild(f"the column {cat.name!r} repeats names, so one bar per name would need a sum or a mean "
                          "the request did not ask for")
    measures = [_by_name(table, n) for n in named if _by_name(table, n).kind in ("number", "integer")]
    rules = []
    if not measures:
        measures, rule = one_scale(_numeric(table))
        if rule:
            rules.append(rule)
    if not measures:
        raise CannotBuild("a bar chart needs a column of numbers")
    if len(measures) > MAX_BAR_SERIES:
        rules.append(f"the first {MAX_BAR_SERIES} of {len(measures)} numeric columns are drawn: "
                     f"{', '.join(c.name for c in measures[MAX_BAR_SERIES:])} left out")
        measures = measures[:MAX_BAR_SERIES]
    order = list(range(len(labels)))
    if len(labels) > MAX_BARS:
        first = np.asarray(measures[0].values, dtype=np.float64)
        order = sorted(order, key=lambda i: (-(first[i] if math.isfinite(first[i]) else -math.inf), i))[:MAX_BARS]
        order.sort()
        rules.append(f"the {MAX_BARS} names with the largest {measures[0].name} of {len(labels)} are drawn, in the "
                     "file's order")
    series = [{"name": _cut(c.name, 200), "values": [_num(np.asarray(c.values, dtype=np.float64)[i]) for i in order]}
              for c in measures]
    desc = f"A bar chart of {', '.join(c.name for c in measures)} for each {cat.name}, from {piece.label}."
    data = {"categories": [_cut(labels[i], 200) for i in order], "x": {**_axis(cat), "scale": "band"},
            "y": _y_axis(measures), "series": series}
    return Built(_spec("chart.bar", f"{', '.join(c.name for c in measures)} by {cat.name}", desc, data, pieces,
                       rules=rules),
                 {"categories": cat.name, "y": [c.name for c in measures], "bars": len(order)}, tuple(rules))


def _nice(step: float) -> float:
    if step <= 0:
        return 1.0
    k = math.floor(math.log10(step))
    for m in (1, 2, 2.5, 5, 10):
        if m * 10 ** k >= step - 1e-12 * 10 ** k:
            return float(m * 10 ** k)
    return float(10 ** (k + 1))


def build_histogram(pieces, request, options) -> Built:
    table = _table(_one(pieces))
    piece = pieces[0]
    named = [n for n in named_in(request, [c.name for c in table.columns])
             if _by_name(table, n).kind in ("number", "integer")]
    numeric = _numeric(table)
    col = _by_name(table, named[0]) if named else (numeric[0] if numeric else None)
    if col is None:
        raise CannotBuild("a histogram needs a column of numbers")
    v = np.asarray(col.values, dtype=np.float64)
    v = v[np.isfinite(v)]
    if v.size < 2:
        raise CannotBuild(f"the column {col.name!r} has fewer than two values")
    lo, hi = float(v.min()), float(v.max())
    bins = int(min(MAX_HIST_BINS, max(MIN_HIST_BINS, math.ceil(math.sqrt(v.size)))))
    width = _nice((hi - lo) / bins) if hi > lo else 1.0
    rules = [f"bins of width {width:g}: the square root of the {v.size} values ({bins} bins, between "
             f"{MIN_HIST_BINS} and {MAX_HIST_BINS}) over their range, widened to a round width"]
    if len(numeric) > 1 and not named:
        rules.append(f"the first of the {len(numeric)} numeric columns is counted ({col.name})")
    if col.n_missing:
        rules.append(f"{col.n_missing} missing value(s) are not counted")
    desc = f"A histogram of the {v.size} values of {col.name} in {piece.label}."
    data = {"x": _axis(col, scale="linear"), "y": {"label": "number of rows"}, "name": _cut(col.name),
            "values": [_num(x) for x in v]}
    return Built(_spec("chart.histogram", f"How {col.name} is spread", desc, data, pieces,
                       options={"bin_width": width}, rules=rules),
                 {"column": col.name, "values": int(v.size), "bin_width": width}, tuple(rules))


def build_heatmap(pieces, request, options) -> Built:
    table = _table(_one(pieces))
    piece = pieces[0]
    numeric = _numeric(table)
    if len(numeric) < 2:
        raise CannotBuild("a heat map needs at least two columns of numbers")
    texts = [c for c in table.columns if c.kind == "text"]
    rules = []
    rows = min(table.n_rows, 200)
    if table.n_rows > 200:
        rules.append(f"the first 200 of {table.n_rows} rows are drawn")
    cols = numeric[:200]
    labels = [str(v) for v in texts[0].values][:rows] if texts else [str(i + 1) for i in range(rows)]
    if len(set(labels)) != len(labels):
        labels = [str(i + 1) for i in range(rows)]
        rules.append("row names repeat, so rows are numbered from 1 instead")
    values = [[_num(np.asarray(c.values, dtype=np.float64)[i]) for c in cols] for i in range(rows)]
    data = {"x": {"label": "column", "categories": [_cut(c.name) for c in cols]},
            "y": {"label": texts[0].name if texts and labels[0] != "1" else "row number",
                  "categories": [_cut(x) for x in labels]},
            "z": {"label": "value"}, "values": values}
    desc = f"A heat map of {len(cols)} numeric columns over {rows} rows of {piece.label}; darker is larger."
    return Built(_spec("chart.heatmap", f"The numbers of {piece.label}", desc, data, pieces, rules=rules),
                 {"columns": [c.name for c in cols], "rows": rows}, tuple(rules))


def build_timeline(pieces, request, options) -> Built:
    piece = _one(pieces)
    rep = piece.representation
    rules = []
    if getattr(rep, "kind", None) == "features":
        events = []
        for i, f in enumerate(rep.features):
            for t in (f.properties.get("times_s") or [])[:1]:
                name = f.properties.get("name")
                events.append({"lane": "events", "at": float(t), "label": _cut(name if isinstance(name, str) and name
                                                                               else f"feature {i + 1}")})
        if len(events) < 2:
            raise CannotBuild(f"{piece.label} has fewer than two dated features")
        data = {"x": {"label": "time", "scale": "time"}, "lanes": ["events"], "events": events[:MAX_EVENTS]}
        return Built(_spec("timeline", f"When, in {piece.label}", f"The first time of each dated feature of "
                           f"{piece.label}.", data, pieces), {"events": len(events)}, ())
    table = _table(piece)
    times = [c for c in table.columns if c.kind == "time"]
    if not times:
        raise CannotBuild("a timeline needs a column of dates or times")
    named = named_in(request, [c.name for c in table.columns])
    t = next((_by_name(table, n) for n in named if _by_name(table, n).kind == "time"), times[0])
    texts = [c for c in table.columns if c.kind == "text"]
    label = next((_by_name(table, n) for n in named if _by_name(table, n).kind == "text"), texts[0] if texts else None)
    lane_col = next((c for c in texts if c is not label and 1 < len(set(c.values)) <= 40), None)
    at = np.asarray(t.values, dtype=np.float64)
    events = []
    for i in range(table.n_rows):
        if not math.isfinite(at[i]):
            continue
        events.append({"lane": _cut(lane_col.values[i]) if lane_col else "events", "at": float(at[i]),
                       "label": _cut(label.values[i]) if label else f"row {i + 1}"})
    if len(events) > MAX_EVENTS:
        rules.append(f"the first {MAX_EVENTS} of {len(events)} dated rows are shown")
        events = events[:MAX_EVENTS]
    if label is None:
        rules.append("the table has no column of names, so each event is labelled by its row number")
    lanes = list(dict.fromkeys(e["lane"] for e in events))
    data = {"x": {"label": _cut(t.name), "scale": "time"}, "lanes": lanes, "events": events}
    desc = f"A timeline of {len(events)} rows of {piece.label} at their {t.name}."
    return Built(_spec("timeline", f"{label.name if label else 'Rows'} by {t.name}", desc, data, pieces, rules=rules),
                 {"time": t.name, "label": label.name if label else None, "events": len(events)}, tuple(rules))


# ---------------------------------------------------------------------------------------------------------------
# Tables as they are


def _table_columns(table, limit_rows) -> tuple[list, list]:
    rules = []
    cols = list(table.columns)
    if len(cols) > MAX_TABLE_COLUMNS:
        rules.append(f"the first {MAX_TABLE_COLUMNS} of {len(cols)} columns are shown")
        cols = cols[:MAX_TABLE_COLUMNS]
    out = []
    for c in cols:
        values = _values(c)[:limit_rows]
        entry = {"name": _cut(c.name), "kind": c.kind, "values": values}
        if c.kind == "integer":
            entry["values"] = [None if v is None else int(v) for v in values]
        if c.unit:
            entry["unit"] = _cut(c.unit, 40)
        out.append(entry)
    return out, rules


def build_table(pieces, request, options) -> Built:
    piece = _one(pieces)
    rep = piece.representation
    kind = getattr(rep, "kind", None)
    if kind == "bundle":
        cols, rules = _bundle_columns(rep, MAX_TABLE_ROWS)
        return Built(_spec("table.html", f"What {piece.label} holds", f"Every member of {piece.label}: its name, "
                           "size and format.", {"columns": cols}, pieces, rules=rules),
                     {"members": len(rep.members)}, tuple(rules))
    if kind == "graph":
        cols, rules = _edge_columns(rep, MAX_TABLE_ROWS)
        return Built(_spec("table.html", f"The links in {piece.label}", f"Every link of {piece.label}: from, to and "
                           "kind.", {"columns": cols}, pieces, rules=rules), {"edges": len(rep.edges)}, tuple(rules))
    table = _table(piece)
    cols, rules = _table_columns(table, MAX_TABLE_ROWS)
    if table.n_rows > MAX_TABLE_ROWS:
        rules.append(f"the first {MAX_TABLE_ROWS} of {table.n_rows} rows are shown (a data file holds them all)")
    desc = f"The table {piece.label} as it is: {table.n_rows} rows, {len(table.columns)} columns."
    return Built(_spec("table.html", table.title or piece.label, desc, {"columns": cols}, pieces, rules=rules),
                 {"rows": min(table.n_rows, MAX_TABLE_ROWS), "columns": len(cols)}, tuple(rules))


def _bundle_columns(rep, limit):
    members = list(rep.members)
    rules = []
    if len(members) > limit:
        rules.append(f"the first {limit} of {len(members)} members are listed")
        members = members[:limit]
    return [{"name": "path", "kind": "text", "values": [str(m.path)[:4096] for m in members]},
            {"name": "size", "kind": "integer", "unit": "bytes", "values": [int(m.size) for m in members]},
            {"name": "format", "kind": "text", "values": [str(m.detected.format) for m in members]},
            {"name": "read", "kind": "boolean", "values": [m.inner is not None for m in members]}], rules


def _edge_columns(rep, limit):
    label = {n.id: n.label for n in rep.nodes}
    edges = list(rep.edges)
    rules = []
    if len(edges) > limit:
        rules.append(f"the first {limit} of {len(edges)} links are listed")
        edges = edges[:limit]
    return [{"name": "from", "kind": "text", "values": [_cut(label.get(e.src, e.src)) for e in edges]},
            {"name": "to", "kind": "text", "values": [_cut(label.get(e.dst, e.dst)) for e in edges]},
            {"name": "kind", "kind": "text", "values": [_cut(e.kind, 40) for e in edges]}], rules


def build_data_file(pieces, request, options) -> Built:
    piece = _one(pieces)
    rep = piece.representation
    kind = getattr(rep, "kind", None)
    if kind == "bundle":
        cols, rules = _bundle_columns(rep, 100_000)
        cols = cols[:3]
        return Built(_spec("data.csv", f"Index of {piece.label}", f"One row per member of {piece.label}: path, size "
                           "in bytes and the format detected.", {"columns": cols}, pieces, rules=rules),
                     {"members": len(cols[0]["values"])}, tuple(rules))
    if kind == "graph":
        cols, rules = _edge_columns(rep, 100_000)
        return Built(_spec("data.csv", f"Links of {piece.label}", f"One row per link of {piece.label}.",
                           {"columns": cols}, pieces, rules=rules), {"edges": len(cols[0]["values"])}, tuple(rules))
    if kind == "mesh":
        return build_mesh_file(pieces, request, options)
    table = _table(piece)
    cols, rules = _table_columns(table, 100_000)
    if table.n_rows > 100_000:
        rules.append(f"the first 100000 of {table.n_rows} rows are written")
    return Built(_spec("data.csv", table.title or piece.label, f"The table {piece.label} as an exact CSV file.",
                       {"columns": cols}, pieces, rules=rules), {"rows": min(table.n_rows, 100_000)}, tuple(rules))


# ---------------------------------------------------------------------------------------------------------------
# Graphs, trees, documents, archives -> diagrams


def _ident(i: int) -> str:
    return f"n{i}"


def _module_of(node_id: str, modules: set) -> str | None:
    """The module a code node belongs to: its own id for a module, else the longest module whose dotted name
    prefixes the node's qualified name."""
    if node_id.startswith("module:"):
        return node_id
    kind, _, qual = node_id.partition(":")
    if kind not in ("function", "class"):
        return None
    parts = qual.split(".")
    for k in range(len(parts) - 1, 0, -1):
        cand = "module:" + ".".join(parts[:k])
        if cand in modules:
            return cand
    return None


def _code_modules(rep) -> tuple[list, list, list] | None:
    """A code graph reduced to its modules: (module nodes, external import nodes, module -> module/import edges).
    None when the graph has no module nodes."""
    modules = {n.id for n in rep.nodes if n.kind == "module"}
    if not modules:
        return None
    by_id = {n.id: n for n in rep.nodes}
    edges = set()
    for e in rep.edges:
        if e.kind != "imports":
            continue
        a = _module_of(e.src, modules)
        b = e.dst if e.dst.startswith("import:") else _module_of(e.dst, modules)
        if a and b and a != b:
            edges.add((a, b))
    externals = sorted({b for _, b in edges if b.startswith("import:")})
    return ([by_id[m] for m in sorted(modules)], [by_id[x] for x in externals if x in by_id], sorted(edges))


def build_nodelink(pieces, request, options) -> Built:
    piece = _one(pieces)
    rep = piece.representation
    kind = getattr(rep, "kind", None)
    if kind == "document" or kind == "tree" or kind == "bundle":
        nodes, parents, rules, what = _hierarchy(piece)
        data = {"nodes": [{"id": _ident(i), "label": _cut(n)} for i, n in enumerate(nodes)],
                "edges": [{"from": _ident(p), "to": _ident(i)} for i, p in enumerate(parents) if p is not None],
                "directed": True}
        return Built(_spec("graph.nodelink", f"The outline of {piece.label}", f"The {what} of {piece.label} as "
                           "nodes, each linked from the one it sits under.", data, pieces, rules=rules),
                     {"nodes": len(nodes), "what": what}, tuple(rules))
    if kind != "graph":
        raise CannotBuild(f"{piece.label} is not a network of things")
    rules = []
    reduced = _code_modules(rep) if len(rep.nodes) > 40 or "module" in _norm(request) else None
    if reduced is not None:
        modules, externals, edges = reduced
        hidden = len(rep.nodes) - len(modules) - len(externals)
        rules.append(f"shown are the {len(modules)} modules and the {len(externals)} outside modules they import; "
                     f"the {hidden} classes, functions and unresolved names inside them are left out, and an import "
                     "of a function counts as an import of its module")
        keep = modules + externals
        ids = {n.id: _ident(i) for i, n in enumerate(keep)}
        nodes = [{"id": ids[n.id], "label": _cut(n.label), "kind": "outside module" if n.kind == "import" else "module"}
                 for n in keep]
        links = [{"from": ids[a], "to": ids[b], "kind": "imports"} for a, b in edges if a in ids and b in ids]
        what = "how its modules import each other and outside modules"
    else:
        keep = list(rep.nodes)
        if len(keep) > MAX_NODES:
            degree: dict[str, int] = {}
            for e in rep.edges:
                degree[e.src] = degree.get(e.src, 0) + 1
                degree[e.dst] = degree.get(e.dst, 0) + 1
            chosen = set(sorted((n.id for n in keep), key=lambda i: (-degree.get(i, 0), i))[:MAX_NODES])
            rules.append(f"the {MAX_NODES} nodes with the most links of {len(keep)} are shown")
            keep = [n for n in keep if n.id in chosen]
        ids = {n.id: _ident(i) for i, n in enumerate(keep)}
        nodes = [{"id": ids[n.id], "label": _cut(n.label), "kind": _cut(n.kind, 40)} for n in keep]
        links = [{"from": ids[e.src], "to": ids[e.dst], "kind": _cut(e.kind, 40)} for e in rep.edges
                 if e.src in ids and e.dst in ids]
        what = "nodes and links"
    seen = set()
    unique = []
    for link in links:
        key = (link["from"], link["to"], link.get("kind"))
        if key not in seen:
            seen.add(key)
            unique.append(link)
    data = {"nodes": nodes, "edges": unique, "directed": True}
    desc = f"A node-link diagram of {piece.label}: {what}."
    return Built(_spec("graph.nodelink", f"How the parts of {piece.label} link", desc, data, pieces, rules=rules),
                 {"nodes": len(nodes), "links": len(unique), "what": what}, tuple(rules))


def _hierarchy(piece, folders_only: bool = False) -> tuple[list[str], list[int | None], list[str], str]:
    """(labels, parent index or None, rules, what it is) of a document's outline, a tree's nodes or an archive's
    folders and members, in reading order. `folders_only`: an archive's folders, each labelled with how many files it
    holds directly, and only the files at its top level (build_tree, when the members are too many to draw)."""
    rep = piece.representation
    kind = rep.kind
    labels: list[str] = []
    parents: list[int | None] = []
    rules: list[str] = []
    if kind == "document":
        stack: list[tuple[int, int]] = []
        for b in rep.blocks:
            if b.kind != "heading":
                continue
            while stack and stack[-1][0] >= b.level:
                stack.pop()
            parents.append(stack[-1][1] if stack else None)
            labels.append(" ".join(b.text.split()))
            stack.append((b.level, len(labels) - 1))
        if not labels:
            raise CannotBuild(f"{piece.label} has no headings to outline")
        what = "headings"
    elif kind == "tree":
        stack = [(rep.root, None)]
        while stack and len(labels) < 5_000_000:
            node, parent = stack.pop()
            labels.append(node.name if node.value is None or isinstance(node.value, (dict, list))
                          else f"{node.name}: {_cut(node.value, 80)}")
            parents.append(parent)
            me = len(labels) - 1
            stack.extend((child, me) for child in reversed(node.children))
        what = "nested items"
    elif kind == "bundle":
        index: dict[str, int] = {}
        files: dict[int, int] = {}
        for m in rep.members:
            if m.path.startswith("("):
                continue
            parts = [p for p in str(m.path).split("/") if p]
            parent = None
            for depth in range(len(parts)):
                key = "/".join(parts[:depth + 1])
                last = depth == len(parts) - 1
                if folders_only and last and parent is not None and not str(m.path).endswith("/"):
                    files[parent] = files.get(parent, 0) + 1
                    break
                if key not in index:
                    labels.append(parts[depth] + ("/" if not last or str(m.path).endswith("/") else ""))
                    parents.append(parent)
                    index[key] = len(labels) - 1
                parent = index[key]
        for i, n in files.items():
            labels[i] += f" ({n} file{'s' if n != 1 else ''})"
        what = "folders, each with how many files it holds," if folders_only else "folders and members"
    else:
        raise CannotBuild(f"{piece.label} has no hierarchy")
    labels, parents = _few_siblings(labels, parents, rules, what)
    if len(labels) > MAX_TREE_NODES:
        rules.append(f"the first {MAX_TREE_NODES} of {len(labels)} {what}, in reading order, are shown")
        labels, parents = labels[:MAX_TREE_NODES], parents[:MAX_TREE_NODES]
    return labels, parents, rules, what


def _few_siblings(labels, parents, rules, what):
    """At most MAX_SIBLINGS children of one node, the first in reading order; the others (with what is under them)
    become one node saying how many are not shown (a diagram of thousands of siblings cannot be read or drawn)."""
    counts: dict = {}
    for p in parents:
        counts[p] = counts.get(p, 0) + 1
    if max(counts.values(), default=0) <= MAX_SIBLINGS:
        return labels, parents
    seen: dict = {}
    new_index, out_labels, out_parents, more = {}, [], [], {}
    for i, (label, p) in enumerate(zip(labels, parents, strict=True)):
        if p is not None and p not in new_index:
            continue                                   # under a node that is not shown
        seen[p] = seen.get(p, 0) + 1
        if seen[p] > MAX_SIBLINGS:
            more[p] = more.get(p, 0) + 1
            continue
        new_index[i] = len(out_labels)
        out_labels.append(label)
        out_parents.append(None if p is None else new_index[p])
    for p, n in more.items():
        out_labels.append(f"... and {n} more")
        out_parents.append(None if p is None else new_index[p])
    rules.append(f"at most {MAX_SIBLINGS} {what} are shown under one node, the first in reading order; one node "
                 "under each says how many more there are")
    return out_labels, out_parents


def _too_wide(labels, parents) -> bool:
    """Whether the tree diagram of these nodes would be wider than its canvas allows, by the generator's own layout
    rule (make/graphs.tidy_tree: one slot as wide as the widest node per leaf)."""
    from .make.graphs import MAX_CANVAS, NODE_GAP, Box
    if not labels:
        return False
    leaves = len(labels) - len({p for p in parents if p is not None})
    slot = max(Box("", _cut(n)).w for n in labels) + NODE_GAP * 0.6
    return leaves * slot + 48 > MAX_CANVAS


def build_tree(pieces, request, options) -> Built:
    piece = _one(pieces)
    rep = piece.representation
    if getattr(rep, "kind", None) == "graph":
        return _code_tree(piece)
    labels, parents, rules, what = _hierarchy(piece)
    if getattr(rep, "kind", None) == "bundle" and _too_wide(labels, parents):
        # a member per node cannot be drawn (code review N4: 5,000 files make a tree 36,814 px wide): the folders,
        # each with how many files it holds, are drawn instead
        labels, parents, rules, what = _hierarchy(piece, folders_only=True)
        rules.insert(0, f"{piece.label} has too many members to draw one by one: only its folders are shown, each "
                        "with how many files it holds directly, and the files at its top level")
    if len([p for p in parents if p is None]) > 1:
        # a tree has one root: the source itself holds the top level
        labels = [piece.label] + labels
        parents = [None] + [0 if p is None else p + 1 for p in parents]
        rules.append(f"the top-level {what} hang under one node named after {piece.label}")
    nodes = [{"id": _ident(i), "label": _cut(n), "parent": None if p is None else _ident(p)}
             for i, (n, p) in enumerate(zip(labels, parents, strict=True))]
    return Built(_spec("graph.tree", f"The outline of {piece.label}", f"A tree of the {what} of {piece.label}, in "
                       "reading order.", {"nodes": nodes}, pieces, rules=rules),
                 {"nodes": len(nodes), "what": what}, tuple(rules))


def _code_tree(piece) -> Built:
    rep = piece.representation
    parent: dict[str, str] = {}
    for e in rep.edges:
        if e.kind == "defines" and e.dst not in parent:
            parent[e.dst] = e.src
    keep = [n for n in rep.nodes if n.kind in ("module", "class", "function")]
    if not keep:
        raise CannotBuild(f"{piece.label} defines nothing to outline")
    rules = []
    if len(keep) > MAX_TREE_NODES - 1:
        rules.append(f"the first {MAX_TREE_NODES - 1} of {len(keep)} definitions are shown")
        keep = keep[:MAX_TREE_NODES - 1]
    ids = {n.id: _ident(i + 1) for i, n in enumerate(keep)}
    nodes = [{"id": "n0", "label": _cut(piece.label), "parent": None}]
    for n in keep:
        up = parent.get(n.id)
        nodes.append({"id": ids[n.id], "label": _cut(n.label), "parent": ids.get(up, "n0"), "kind": n.kind})
    rules.append("the definitions hang under the module or class that defines them; imports and calls are left out")
    return Built(_spec("graph.tree", f"What {piece.label} defines", f"The modules, classes and functions of "
                       f"{piece.label}.", {"nodes": nodes}, [piece], rules=rules), {"nodes": len(nodes)}, tuple(rules))


# ---------------------------------------------------------------------------------------------------------------
# Sound


def _envelope(sig) -> tuple[np.ndarray, np.ndarray, str]:
    x = sig.samples.astype(np.float64)
    mono = np.abs(x).max(axis=1) if x.shape[1] > 1 else np.abs(x[:, 0])
    n = len(mono)
    windows = max(1, min(ENVELOPE_WINDOWS, n))
    edges = np.linspace(0, n, windows + 1).astype(np.int64)
    peak = np.array([mono[a:b].max() if b > a else 0.0 for a, b in zip(edges[:-1], edges[1:], strict=True)])
    t = edges[:-1] / sig.rate_hz
    rule = (f"the largest absolute sample of {'any channel' if x.shape[1] > 1 else 'the recording'} in each of "
            f"{windows} equal windows of about {n / windows / sig.rate_hz * 1000:.3g} ms")
    return t, peak, rule


def _spectrum(sig) -> tuple[np.ndarray, np.ndarray, str]:
    x = sig.samples.astype(np.float64).mean(axis=1)
    n = len(x)
    size = SPECTRUM_FFT if n >= SPECTRUM_FFT else max(16, 1 << max(4, (n - 1).bit_length() - 1))
    size = min(size, n) if n >= 16 else n
    if n < 16:
        raise CannotBuild("the recording is too short for a spectrum")
    hop = size // 2
    starts = np.arange(0, n - size + 1, hop)
    if len(starts) > SPECTRUM_MAX_WINDOWS:
        starts = starts[np.linspace(0, len(starts) - 1, SPECTRUM_MAX_WINDOWS).astype(np.int64)]
    win = np.hanning(size)
    power = np.zeros(size // 2 + 1)
    for s in starts:
        power += np.abs(np.fft.rfft(x[s:s + size] * win)) ** 2
    power /= max(1, len(starts))
    freq = np.fft.rfftfreq(size, 1.0 / sig.rate_hz)
    top = power.max()
    level = 10 * np.log10(np.maximum(power, top * 1e-12) / top) if top > 0 else np.full_like(power, -120.0)
    rule = (f"the mean power of {len(starts)} Hann-windowed stretches of {size} samples (half overlapping), in "
            "decibels below the strongest frequency; levels below -120 dB are drawn at -120")
    return freq, np.maximum(level, -120.0), rule


def _peaks(freq, level, k=6) -> list[tuple[float, float]]:
    is_peak = np.r_[False, (level[1:-1] > level[:-2]) & (level[1:-1] >= level[2:]), False]
    idx = np.flatnonzero(is_peak & (level > -40))
    idx = sorted(idx, key=lambda i: -level[i])[:k]
    return sorted((float(freq[i]), float(level[i])) for i in idx)


def _envelope_chart(piece, options) -> Built:
    t, peak, rule = _envelope(piece.representation)
    data = {"x": {"label": "time", "unit": "s"}, "y": {"label": "amplitude (1 is full scale)"},
            "series": [{"name": "largest absolute sample", "points": [[_num(a), _num(b)] for a, b in zip(t, peak,
                                                                                                    strict=True)]}]}
    desc = f"How loud {piece.label} is over its {t[-1] if len(t) else 0:.3g} seconds."
    return Built(_spec("chart.line", f"Loudness of {piece.label} over time", desc, data, [piece], rules=[rule]),
                 {"measure": "envelope", "windows": len(t)}, (rule,))


def build_sound_view(pieces, request, options) -> Built:
    piece = _one(pieces)
    sig = piece.representation
    if getattr(sig, "kind", None) != "signal":
        raise CannotBuild(f"{piece.label} is not a recording")
    env = _envelope_chart(piece, options)
    freq, level, srule = _spectrum(sig)
    keep = freq <= sig.rate_hz / 2
    spectrum = _spec("chart.line", f"Frequencies in {piece.label}", f"The spectrum of {piece.label}.",
                     {"x": {"label": "frequency", "unit": "Hz"}, "y": {"label": "level", "unit": "dB"},
                      "series": [{"name": "level", "points": [[_num(a), _num(b)] for a, b in
                                                              zip(freq[keep], level[keep], strict=True)]}]},
                     [piece], rules=[srule])
    peaks = _peaks(freq, level)
    n, ch = sig.samples.shape
    blocks = [{"kind": "heading", "level": 1, "text": "Loudness over time"},
              {"kind": "paragraph", "text": f"{n} samples per channel, {ch} channel(s), {sig.rate_hz:g} samples a "
                                            f"second: {n / sig.rate_hz:.3g} seconds. The envelope is {env.rules[0]}."},
              {"kind": "figure", "id": "envelope", "caption": "Loudness over time", "figure": env.spec},
              {"kind": "heading", "level": 1, "text": "Frequencies"},
              {"kind": "paragraph", "text": f"The spectrum is {srule}."},
              {"kind": "figure", "id": "spectrum", "caption": "Spectrum", "figure": spectrum}]
    if peaks:
        blocks += [{"kind": "paragraph", "text": "The strongest frequencies (local maxima within 40 dB of the "
                                                 "strongest), as measured:"},
                   {"kind": "table", "id": "peaks", "caption": "Strongest frequencies",
                    "table": {"columns": [{"name": "frequency", "kind": "number", "unit": "Hz",
                                           "values": [round(f, 1) for f, _ in peaks]},
                                          {"name": "level", "kind": "number", "unit": "dB",
                                           "values": [round(v, 1) for _, v in peaks]}]}}]
    rules = (env.rules[0], srule)
    spec = _spec("document.html", f"The sound of {piece.label}", f"A waveform-and-spectrum view of {piece.label}: "
                 "its loudness over time and its frequencies, both measured from its samples.", {"blocks": blocks},
                 pieces)
    return Built(spec, {"envelope_windows": len(env.spec["data"]["series"][0]["points"]),
                        "spectrum_points": int(keep.sum()), "peaks": len(peaks)}, rules)


def build_sonify(pieces, request, options) -> Built:
    piece = _one(pieces)
    rep = piece.representation
    if getattr(rep, "kind", None) == "signal":
        raise CannotBuild("a recording is already a sound; playing given samples back is not a form here")
    table = _table(piece)
    named = [n for n in named_in(request, [c.name for c in table.columns])
             if _by_name(table, n).kind in ("number", "integer")]
    numeric = _numeric(table)
    col = _by_name(table, named[0]) if named else (numeric[0] if numeric else None)
    if col is None:
        raise CannotBuild("a sonification needs a column of numbers")
    values = [_num(v) for v in np.asarray(col.values, dtype=np.float64)][:10_000]
    rules = []
    if table.n_rows > 10_000:
        rules.append(f"the first 10000 of {table.n_rows} values are heard")
    if len(numeric) > 1 and not named:
        rules.append(f"the first of the {len(numeric)} numeric columns is heard ({col.name})")
    data = {"values": values, "name": _cut(col.name, 100)}
    if col.unit:
        data["unit"] = _cut(col.unit, 32)
    desc = f"The {len(values)} values of {col.name} in {piece.label}, one tone each, higher pitch for larger values."
    return Built(_spec("sound.sonify", f"{col.name}, heard", desc, data, pieces, rules=rules),
                 {"column": col.name, "values": len(values)}, tuple(rules))


# ---------------------------------------------------------------------------------------------------------------
# Solids and pictures


def _mesh(piece):
    rep = piece.representation
    if getattr(rep, "kind", None) != "mesh":
        raise CannotBuild(f"{piece.label} is not a mesh")
    if not len(rep.faces):
        raise CannotBuild(f"{piece.label} has no faces")
    return rep


def build_solid_view(pieces, request, options) -> Built:
    piece = _one(pieces)
    rep = _mesh(piece)
    if len(rep.vertices) > MAX_SOLID_VIEW_VERTICES or len(rep.faces) > MAX_SOLID_VIEW_FACES:
        raise CannotBuild(f"the viewer shows at most {MAX_SOLID_VIEW_VERTICES} vertices and {MAX_SOLID_VIEW_FACES} "
                          f"faces; {piece.label} has {len(rep.vertices)} and {len(rep.faces)} (no simplifier exists "
                          "here)")
    data = {"vertices": [[float(v) for v in row] for row in rep.vertices],
            "faces": [[int(i) for i in row] for row in rep.faces]}
    from .make.solids import UNITS
    rules = []
    if rep.unit in UNITS:
        data["unit"] = rep.unit
    else:
        rules.append("the file states no unit this viewer knows, so sizes are given without one")
    desc = f"The solid of {piece.label} ({len(rep.vertices)} vertices, {len(rep.faces)} triangles), to turn and tilt."
    return Built(_spec("solid.view", f"The shape of {piece.label}", desc, data, pieces, rules=rules),
                 {"vertices": len(rep.vertices), "faces": len(rep.faces)}, tuple(rules))


def build_mesh_file(pieces, request, options) -> Built:
    piece = _one(pieces)
    rep = _mesh(piece)
    from .make.solids import UNITS
    data = {"vertices": [[float(v) for v in row] for row in rep.vertices],
            "faces": [[int(i) for i in row] for row in rep.faces]}
    opts = {"format": options.get("format", "stl") if options.get("format") in ("stl", "obj") else "stl"}
    rules = []
    if rep.unit in UNITS:
        opts["unit"] = rep.unit
    else:
        opts["unit"] = "unitless"
        rules.append("the file states no unit, so the mesh is written as unitless")
    desc = f"The {len(rep.faces)} triangles of {piece.label} as a {opts['format'].upper()} file."
    return Built(_spec("solid.mesh", f"{piece.label} as {opts['format'].upper()}", desc, data, pieces, options=opts,
                       rules=rules), {"vertices": len(rep.vertices), "faces": len(rep.faces)}, tuple(rules))


def build_image(pieces, request, options) -> Built:
    piece = _one(pieces)
    rep = piece.representation
    if getattr(rep, "kind", None) != "raster":
        raise CannotBuild(f"{piece.label} is not a picture")
    from . import measure_image
    edges = options.get("measure") == "edges" or bool(re.search(r"\bedges?\b", request.lower()))
    if edges:
        grid = measure_image.sobel_magnitude(rep)
        measure = "edge strength (the Sobel gradient magnitude of the luminance)"
    else:
        grid = measure_image.luminance(rep)
        measure = "luminance (0.2126 red + 0.7152 green + 0.0722 blue of the linear-scaled samples, 0 to 1)"
    h, w = grid.shape
    step = max(1, math.ceil(max(h, w) / RASTER_SIDE))
    rules = [f"each cell shows {measure}"]
    if step > 1:
        hh, ww = (h // step) * step, (w // step) * step
        grid = grid[:hh, :ww].reshape(hh // step, step, ww // step, step).mean(axis=(1, 3))
        rules.append(f"each cell is the mean of a {step} by {step} block of pixels ({w} by {h} pixels became "
                     f"{grid.shape[1]} by {grid.shape[0]} cells; a last partial row or column of blocks is left out)")
    values = [[_num(round(float(v), 6)) for v in row] for row in grid]
    desc = f"{('Edge strength' if edges else 'Luminance')} of {piece.label}, darker for lower values."
    return Built(_spec("image.raster", f"{'Edges' if edges else 'Light'} in {piece.label}", desc, {"grid": values},
                       pieces, options={"range": [0, max(1.0, float(np.nanmax(grid)) if grid.size else 1.0)]
                                        if edges else [0, 1], "ramp": "dark_low"}, rules=rules),
                 {"measure": "edges" if edges else "luminance", "cells": [len(values[0]), len(values)]},
                 tuple(rules))


def build_explorable(pieces, request, options) -> Built:
    piece = _one(pieces)
    rep = piece.representation
    model = getattr(rep, "model", None)
    if not isinstance(model, dict):
        raise CannotBuild("an explorable model is made only from a model file the owner gives (neuroscifi.model/1); "
                          "it is never guessed from data")
    title = getattr(rep, "title", "") or piece.label
    desc = getattr(rep, "description", "") or f"The model of {piece.label}, with its inputs to vary."
    return Built(_spec("explorable.model", title, desc, model, pieces),
                 {"parameters": len(model.get("parameters", [])), "views": len(model.get("views", []))}, ())


# ---------------------------------------------------------------------------------------------------------------
# Documents


def facts_sentences(piece, limit=12) -> list[str]:
    """The piece's measured facts (store.facts_of) as plain sentences."""
    from .store import facts_of
    out = []
    for fact in facts_of(piece.representation)[:limit]:
        value = fact["value"]
        if isinstance(value, dict):
            parts = [f"{k} {_fmt(v)}" for k, v in value.items() if k != "column"]
            head = f"column {value['column']}" if "column" in value else fact["name"]
            out.append(f"{head}: {', '.join(parts)}.")
        else:
            out.append(f"{fact['name'].replace('_', ' ')}: {_fmt(value)} ({fact['how']}).")
    return out


def _fmt(v) -> str:
    if isinstance(v, float):
        return f"{v:.6g}"
    if isinstance(v, list):
        return ", ".join(_fmt(x) for x in v[:8]) + (" …" if len(v) > 8 else "")
    return str(v)


def about(piece, *, named: bool = True) -> str:
    """One sentence on what a piece is, from its measurements (without its name when `named` is False)."""
    rep, kind = piece.representation, getattr(piece.representation, "kind", "")
    fmt = piece.format.upper() if len(piece.format) <= 5 else piece.format
    if kind == "table":
        names = ", ".join(c.name for c in rep.columns[:8]) + (" …" if len(rep.columns) > 8 else "")
        what = f"a table of {rep.n_rows} rows and {len(rep.columns)} columns ({names})"
    elif kind == "signal":
        n, ch = rep.samples.shape
        what = (f"a recording of {n / rep.rate_hz:.3g} seconds, {ch} channel{'s' if ch > 1 else ''}, "
                f"{rep.rate_hz:g} samples a second")
    elif kind == "graph":
        what = f"a network of {len(rep.nodes)} nodes and {len(rep.edges)} links"
    elif kind == "document":
        what = f"a document of {len(rep.blocks)} blocks"
    elif kind == "mesh":
        what = f"a mesh of {len(rep.vertices)} vertices and {len(rep.faces)} triangles"
    elif kind == "raster":
        what = f"a picture of {rep.size[0]} by {rep.size[1]} pixels"
    elif kind == "bundle":
        what = f"an archive of {len(rep.members)} members"
    else:
        what = f"{kind or 'content'}"
    reading = "" if piece.reading == "as decoded" else f", read {piece.reading}"
    text = f"{what} ({fmt}{reading})."
    return f"{piece.label} is {text}" if named else text[:1].upper() + text[1:]


def _title(pieces) -> str:
    labels = list(dict.fromkeys(p.label for p in pieces))
    if len(labels) <= 3:
        return labels[0] if len(labels) == 1 else ", ".join(labels[:-1]) + " and " + labels[-1]
    return f"{', '.join(labels[:2])} and {len(labels) - 2} more"


def build_document(pieces, request, options, *, parts=None, relations=None) -> Built:
    """A document: for one Document piece, its digest (outline, tables, figure list); for several pieces (or `parts`
    the production made for each piece), one section per piece with its made figure or table and its sources;
    `relations` (blocks the production wrote on how its sources relate) form their own section after them."""
    if parts is None and len(pieces) == 1 and getattr(pieces[0].representation, "kind", None) == "document":
        return _digest(pieces[0])
    blocks = []
    rules = []
    used = {"sections": []}
    for i, piece in enumerate(pieces):
        part = (parts or {}).get(i)
        blocks.append({"kind": "heading", "level": 1, "text": _cut(piece.label, 4096)})
        blocks.append({"kind": "paragraph", "text": _cut(about(piece), 4096)})
        if part is not None and part.get("figure") is not None:
            blocks.append({"kind": "figure", "id": f"f{i + 1}", "caption": _cut(part["figure"]["title"]),
                           "figure": part["figure"]})
            if part.get("rule"):
                blocks.append({"kind": "note", "text": _cut(part["rule"][:1].upper() + part["rule"][1:] + ".", 4096)})
        elif part is not None and part.get("blocks"):
            blocks.extend(part["blocks"])
        elif part is not None and part.get("table") is not None:
            blocks.append({"kind": "table", "id": f"t{i + 1}", "caption": _cut(part.get("caption") or piece.label),
                           "table": part["table"]})
        for sentence in (part or {}).get("facts", facts_sentences(piece) if part is None else []):
            blocks.append({"kind": "list_item", "text": _cut(sentence, 4096)})
        if part is not None and part.get("rule"):
            rules.append(f"{piece.label}: {part['rule']}")
        used["sections"].append({"source": piece.source, "shown_as": (part or {}).get("shown_as", "facts")})
    if relations:
        blocks.extend(relations)
        used["relations"] = sum(1 for b in relations if b["kind"] == "heading" and b["level"] == 2)
    sources = [{"title": _cut(p.label), "detail": _cut(about(p, named=False), 1000)} for p in pieces]
    spec = _spec("document.html", _title(pieces), f"One section for each of the {len(pieces)} sources, each shown "
                 "in the form planned for it on its own.", {"blocks": blocks, "sources": sources}, pieces,
                 rules=rules)
    return Built(spec, used, tuple(rules))


def _digest(piece) -> Built:
    rep = piece.representation
    blocks, rules = [], []
    figures, tables, paragraphs, words = [], 0, 0, 0
    current = None
    for b in rep.blocks:
        if b.kind == "heading":
            if current is not None and current[1]:
                blocks.append({"kind": "paragraph", "text": f"{current[1]} paragraph(s), {current[2]} words."})
            blocks.append({"kind": "heading", "level": max(1, min(5, b.level)), "text": _cut(b.text, 4096)})
            current = [b.text, 0, 0]
        elif b.kind == "paragraph":
            paragraphs += 1
            n = len(b.text.split())
            words += n
            if current is None:
                current = ["", 0, 0]
            current[1] += 1
            current[2] += n
        elif b.kind == "table" and b.ref is not None and tables < 20:
            cols, _ = _table_columns(b.ref, 200)
            if cols and any(c["values"] for c in cols):
                tables += 1
                blocks.append({"kind": "table", "id": f"t{tables}", "caption": _cut(b.text or f"Table {tables} of "
                                                                                    f"{piece.label}"),
                               "table": {"columns": cols}})
        elif b.kind == "figure":
            figures.append(b.text or (str(b.ref) if b.ref else "a figure without a caption"))
    if current is not None and current[1]:
        blocks.append({"kind": "paragraph", "text": f"{current[1]} paragraph(s), {current[2]} words."})
    # levels may not skip (document.html's rule): pull each heading up to one below the previous
    last = 0
    for b in blocks:
        if b["kind"] == "heading":
            b["level"] = min(b["level"], last + 1)
            last = b["level"]
    if figures:
        blocks.append({"kind": "heading", "level": 1, "text": "Figures in the document"})
        blocks.extend({"kind": "list_item", "text": _cut(f, 4096)} for f in figures[:200])
    if not blocks:
        raise CannotBuild(f"{piece.label} has no headings, tables or figures to digest")
    rules.append("each section's paragraphs are counted, not quoted; tables are copied with up to 200 rows; figures "
                 "are listed by their captions")
    spec = _spec("document.html", rep.title or piece.label, f"A digest of {piece.label}: its outline, "
                 f"{tables} table(s) and {len(figures)} figure(s); {paragraphs} paragraphs, {words} words in all.",
                 {"blocks": blocks}, [piece], rules=rules)
    return Built(spec, {"headings": sum(1 for b in blocks if b["kind"] == "heading"), "tables": tables,
                        "figures": len(figures)}, tuple(rules))


# ---------------------------------------------------------------------------------------------------------------
# The registry: planner form name (forms.FORMS) or a generator form with no planner form -> builder

BUILDERS: dict[str, Callable] = {
    "line_chart": build_line, "small_multiples": build_multiples, "bar_chart": build_bar,
    "scatter_chart": build_scatter, "histogram": build_histogram, "heatmap": build_heatmap,
    "timeline": build_timeline, "table": build_table, "data_file": build_data_file, "node_link": build_nodelink,
    "tree_diagram": build_tree, "document": build_document, "sound_view": build_sound_view,
    "sonification": build_sonify, "solid_view": build_solid_view, "image": build_image,
    "explorable": build_explorable,
    # generator forms the planner has no form for (named explicitly, by their generator name)
    "solid.mesh": build_mesh_file,
}
# Which representation kinds each builder can take (the coverage table of the evidence; a builder may still refuse
# a particular piece, by name, when what it needs is not in it).
TAKES = {
    "line_chart": ("table", "signal"), "small_multiples": ("table",), "bar_chart": ("table",),
    "scatter_chart": ("table", "features"), "histogram": ("table",), "heatmap": ("table",),
    "timeline": ("table", "features"), "table": ("table", "bundle", "graph"),
    "data_file": ("table", "bundle", "graph", "mesh"), "node_link": ("graph", "document", "tree", "bundle"),
    "tree_diagram": ("document", "tree", "bundle", "graph"), "document": ("document", "*"),
    "sound_view": ("signal",), "sonification": ("table",), "solid_view": ("mesh",), "image": ("raster",),
    "explorable": ("tree",), "solid.mesh": ("mesh",),
}


def register(form: str, builder: Callable, takes=("*",)) -> None:
    """Add a builder for a form (a new form is one registration)."""
    if form in BUILDERS:
        raise ValueError(f"a builder for {form!r} is already registered")
    BUILDERS[form] = builder
    TAKES[form] = tuple(takes)


def build(form: str, pieces, request: str = "", options: dict | None = None) -> Built:
    builder = BUILDERS.get(form)
    if builder is None:
        raise CannotBuild(f"no builder turns content into {form.replace('_', ' ')} yet")
    if not pieces:
        raise CannotBuild("there is no content to build from")
    return builder(list(pieces), str(request or ""), dict(options or {}))


__all__ = ["BUILDERS", "Built", "CannotBuild", "Piece", "TAKES", "axes_named", "build", "build_document",
           "facts_sentences", "named_in", "register"]
