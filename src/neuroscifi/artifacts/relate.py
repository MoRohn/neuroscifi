"""Relating artifacts of different kinds (lane R; ADR-077): cross-modal binding and reconciliation by MEASUREMENT.

Two artifacts are related here when a quantity or a structure that can be MEASURED in one is STATED or SHOWN in the
other, and the two can be compared. Nothing reads the meaning of a picture or a sound: a chart's values are recovered
through the reading contract (DESIGN section 6, by the independent reader `check/svg_read.py`), a sound's tones are
measured from its samples, a mesh's size from its vertices; a document's statements are its own words, read by the
bounded reader `relate_read.py`.

- `quantities(representation)` -> `Quantity` list: named, typed, unit-carrying values computed from a decoded
  representation, each with how it was computed (counts, column aggregates and series, durations, tones and their
  pitches, bounding boxes, call pairs, events in time).
- `statements(representation, data=None)` -> `Statement` list: what an artifact states or shows (numbers with their
  words and units, sizes, ranges, counts, ordered steps, a log's events, a chart's series, a trace's call pairs).
- `compare(a, b, question=None)` -> `Relation`: candidate pairs of facts by compatible kind, unit and name (column
  and label words, never a list per kind of benchmark case); a numeric or structural comparison with a stated
  tolerance; a verdict per pair (consistent, inconsistent with both values, or not comparable); an overall answer
  only when the question, or the two artifacts, single out what to compare. `answer=None` ("cannot") whenever nothing
  comparable decides it: a wrong "consistent" is this module's fabrication, and it never guesses.
- `timing(a, b)`: two artifacts with time axes, aligned: spans, overlap, offset, order agreement of matched events.
- `shared_names(a, b)`, `mentions(a, b)`: weak evidence of a shared subject (a name in both) and of one artifact
  describing the other (it names the other's file).
- `transfer_units(table, document)`: units a document states for a table's columns, applied with their provenance
  (directive 10.2, measured in `docs/quality/evidence/omnimind_r.json`).

Everything is deterministic, offline, standard library and numpy only. Text taken from an artifact stays untrusted:
it is kept as provenance and cleaned before it is shown, never interpreted beyond the reader's patterns.
"""
from __future__ import annotations

import itertools
import math
import re
from dataclasses import dataclass, field, replace

import numpy as np

from . import relate_read as rr
from .relate_read import Unit, clean, name_words, unit_of

MAX_FACTS = 1500            # per artifact
MAX_PAIRS = 20_000
MAX_COLUMNS = 64            # columns of a table that are measured
MAX_SERIES = 20_000         # values of a series compared point by point
MAX_MATCH = 4_000_000       # steps x lines tried when matching words
MATCH_SHARE = 0.75          # a step matches an event when this share of its words is in the event's line
REL_EPS = 1e-9

ROW_NOUNS = ("row", "record", "entry", "item", "line", "observation")
TONE_NOUNS = ("tone", "note", "beep", "pulse", "segment")
SEQUENCE_NOUNS = ("step", "event", "stage")
GENERIC_NAMES = frozenset(("index", "id", "value", "values", "name", "date", "time", "count", "total", "number",
                           "type", "item", "row", "column", "key", "data", "x", "y", "z", "label", "level"))


# ---- facts -----------------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Fact:
    kind: str                       # count number range extent box series sequence events pairs unit
    ref: str                        # where it is in the artifact, machine-readable ("table.column[2].sum")
    value: object
    unit: Unit | None = None
    words: tuple[str, ...] = ()     # subject words (a column's name, a sentence's content words)
    aggregate: str | None = None    # sum mean median min max first last range count, or None (the value itself)
    nouns: tuple[str, ...] = ()     # what a count counts (singular)
    how: str = ""                   # plain words: how it was computed or where it was read
    tolerance: float = 0.0          # absolute, in the fact's unit
    primary: bool = False           # the artifact's dominant count (rows, tones, ...)
    sentence: str = ""              # a statement's sentence (UNTRUSTED, cleaned), for provenance only
    detail: tuple = ()              # kind-specific (key, value) pairs

    def get(self, key, default=None):
        for k, v in self.detail:
            if k == key:
                return v
        return default


@dataclass(frozen=True)
class Quantity(Fact):
    """Measured from the decoded representation."""
    origin = "measured"


@dataclass(frozen=True)
class Statement(Fact):
    """Stated in an artifact's own words, or shown by its marks (a chart)."""
    origin = "stated"


def _q(kind, ref, value, **kw) -> Quantity:
    return Quantity(kind, ref, value, **kw)


def _s(kind, ref, value, **kw) -> Statement:
    return Statement(kind, ref, value, **kw)


# ---- what a representation measures ----------------------------------------------------------------------------------

def _finite(values) -> np.ndarray:
    a = np.asarray(values, dtype=np.float64)
    return a[np.isfinite(a)]


def _ordinal(values: np.ndarray) -> bool:
    """1..n or 0..n-1 in order: a row number, not a measurement."""
    n = len(values)
    return n >= 2 and (np.array_equal(values, np.arange(1, n + 1)) or np.array_equal(values, np.arange(n)))


def _column_unit(column) -> Unit | None:
    u = unit_of(column.unit) if column.unit else None
    if u is None and column.name and unit_of(column.name.strip()) is not None and len(column.name.strip()) > 2:
        u = unit_of(column.name.strip())                # a column named "Hours" or "Seconds" states its unit
    m = re.fullmatch(r"[A-Za-z][A-Za-z ]*?[_-]([A-Za-z]{1,3})", (column.name or "").strip()) if u is None else None
    if m and unit_of(m.group(1)) is not None and unit_of(m.group(1)).dim in ("length", "mass", "time"):
        u = unit_of(m.group(1))                         # "weight_g", "length_mm", "time_s" (lane FX, item 3)
    return u


def _numeric_columns(table):
    """(index, column, values float64 with NaN, unit) for every column that holds numbers: number and integer
    columns, and text columns whose every cell is one number in one consistent format."""
    out = []
    for i, c in enumerate(table.columns[:MAX_COLUMNS]):
        if c.kind in ("number", "integer"):
            out.append((i, c, np.asarray(c.values, dtype=np.float64), _column_unit(c), ""))
        elif c.kind == "text":
            read = rr.column_numbers(c.values)
            if read is not None:
                values, symbol = read
                unit = _column_unit(c) or (unit_of(symbol) if symbol else None)
                out.append((i, c, np.asarray(values, dtype=np.float64), unit,
                            "its cells are numbers written as text"))
    return out


def _key_column(table, skip: int):
    """The column that labels the rows: the first text, time or distinct integer column other than `skip`."""
    for i, c in enumerate(table.columns):
        if i == skip:
            continue
        if c.kind == "text" and rr.column_numbers(c.values) is None:
            return i, c, "category"
        if c.kind == "time":
            return i, c, "time"
        if c.kind == "integer":
            v = np.asarray(c.values, dtype=np.float64)
            if len(set(v[np.isfinite(v)].tolist())) == len(v):
                return i, c, "number"
    return None


def _table_quantities(t, prefix: str, origin: type, label_words=()) -> list:
    make = _q if origin is Quantity else _s
    # a file's or table's name may say what its rows are (receipts.csv), unless a column carries the same word: then
    # the name says what that column holds (calls.csv with a column of calls, one row per day), not what rows are
    column_words = {w for c in t.columns for w in name_words(c.name)}
    also = tuple(w for w in tuple(label_words) + (name_words(t.title) if t.title else ()) if w not in column_words)
    out = [make("count", f"{prefix}.rows", int(t.n_rows), nouns=ROW_NOUNS, primary=True, aggregate="count",
                how="rows the decoder read", detail=(("also_nouns", also),)),
           make("count", f"{prefix}.columns", len(t.columns), nouns=("column", "field", "variable"),
                aggregate="count", how="columns")]
    numeric = _numeric_columns(t)
    numeric_idx = {i for i, *_ in numeric}
    for i, c in enumerate(t.columns[:MAX_COLUMNS]):
        if c.kind == "text" and i not in numeric_idx:
            words = name_words(c.name)
            distinct = len({v for v in c.values if v != ""})
            if words:
                out.append(make("count", f"{prefix}.column[{i}].distinct", distinct, nouns=words, words=words,
                                aggregate="count", how=f"distinct values of column {i + 1}"))
    for i, c, values, unit, note in numeric:
        fin = _finite(values)
        if not len(fin):
            continue
        words = name_words(c.name)
        ordinal = _ordinal(fin) and len(fin) == len(values)
        detail = (("column", i), ("ordinal", ordinal))
        if not ordinal:
            scale = max(1.0, float(np.abs(fin).max()))
            aggs = {"sum": float(fin.sum()), "mean": float(fin.mean()), "median": float(np.median(fin)),
                    "min": float(fin.min()), "max": float(fin.max()), "first": float(fin[0]), "last": float(fin[-1])}
            for agg, v in aggs.items():
                out.append(make("number", f"{prefix}.column[{i}].{agg}", v, unit=unit, words=words, aggregate=agg,
                                tolerance=REL_EPS * scale * len(fin), how=f"{agg} of column {i + 1}"
                                + (f"; {note}" if note else ""), detail=detail))
            out.append(make("range", f"{prefix}.column[{i}].range", (aggs["min"], aggs["max"]), unit=unit,
                            words=words, aggregate="range", tolerance=REL_EPS * scale,
                            how=f"smallest and largest value of column {i + 1}", detail=detail))
        key = _key_column(t, i)
        if key is not None:
            ki, kc, xkind = key
            xs = kc.values if xkind == "category" else np.asarray(kc.values, dtype=np.float64)
        else:
            ki, xkind, xs = None, "index", np.arange(len(values), dtype=np.float64)
        points = tuple((xs[r] if xkind == "category" else float(xs[r]), float(values[r]))
                       for r in range(len(values)) if math.isfinite(values[r]))
        if not ordinal and len(points) >= 2:
            out.append(make("series", f"{prefix}.column[{i}].series", points, unit=unit, words=words,
                            how=f"the values of column {i + 1} by "
                                + (f"column {ki + 1}" if ki is not None else "row order"),
                            tolerance=REL_EPS * max(1.0, float(np.abs(fin).max())),
                            detail=(("x_kind", xkind), ("column", i), ("log", False))))
    times = [(i, c) for i, c in enumerate(t.columns) if c.kind == "time"]
    texts = [(i, c) for i, c in enumerate(t.columns) if c.kind == "text" and i not in numeric_idx]
    if times and texts:
        ti, tc = times[0]
        evs = []
        for r in range(t.n_rows):
            when = float(tc.values[r])
            if not math.isfinite(when):
                continue
            words_r = " ".join(str(c.values[r]) for _, c in texts)
            evs.append(rr.Event(when, True, clean(words_r), rr.step_words(words_r), r, 1.0))
        if len(evs) >= 2:
            out.append(make("events", f"{prefix}.events", tuple(evs[:rr.MAX_EVENTS]),
                            nouns=("event", "entry", "line", "record"),
                            how=f"rows with a time (column {ti + 1}) and words (the text columns)"))
    elif texts and not times:
        for i, c in texts[:4]:
            items = [str(v) for v in c.values][:rr.MAX_ITEMS]
            if len(items) >= 2 and len(set(items)) == len(items):
                out.append(make("sequence", f"{prefix}.column[{i}].order", rr.Sequence(items, "rows", i),
                                nouns=SEQUENCE_NOUNS, how=f"the rows of column {i + 1} in order"))
    return out


def _signal_quantities(sig) -> list:
    from . import measure_audio as ma
    n, channels = sig.samples.shape
    out = [_q("number", "signal.duration", float(n / sig.rate_hz), unit=unit_of("s"),
              words=("duration", "length", "long", "last", "lasting", "play", "running", "time"),
              tolerance=1.0 / sig.rate_hz, how="samples divided by the sample rate"),
           _q("count", "signal.channels", int(channels), nouns=("channel",), aggregate="count", how="channels"),
           _q("number", "signal.rate", float(sig.rate_hz), unit=unit_of("hz"), words=("sample", "rate"),
              how="the declared sample rate")]
    tones = tone_segments(sig)
    if tones is not None:
        out.append(_q("count", "signal.tones", len(tones), nouns=TONE_NOUNS, aggregate="count", primary=True,
                      how="steady tones separated by silence or by a change of pitch, measured from the samples "
                          "(20 ms quiet gaps; pitch steady within 3 %)"))
        if len(tones) >= 2:
            out.append(_q("series", "signal.pitches", tuple((float(k), t[2]) for k, t in enumerate(tones)),
                          unit=unit_of("hz"), words=("pitch", "frequency", "tone", "note"),
                          how="the frequency of each tone in order (largest spectral peak, interpolated)",
                          tolerance=max(1.0, 0.01 * max(t[2] for t in tones)),
                          detail=(("x_kind", "index"), ("log", False))))
        out.append(_q("times", "signal.tone_starts", tuple(t[0] for t in tones), unit=unit_of("s"),
                      how="start time of each tone (seconds from the start of the file)",
                      detail=(("absolute", False),)))
    else:
        if n and n / sig.rate_hz <= 600:
            onsets = ma.onsets(sig)
            out.append(_q("times", "signal.onsets", tuple(onsets), unit=unit_of("s"),
                          how="onset times by spectral flux (measure_audio.onsets)", detail=(("absolute", False),)))
    return out


def tone_segments(sig, *, gap_s: float = 0.02, steady: float = 0.03) -> list | None:
    """(start_s, end_s, frequency_hz) of each steady tone: sounding stretches between quiet gaps of at least
    `gap_s` (frame level 35 dB under the loudest frame), each split where its pitch moves by more than `steady`.
    None when the sound is not made of steady tones (a stretch whose pitch is not steady over most of it): then no
    tone count is claimed. Bounded to 10 minutes of sound."""
    from . import measure_audio as ma
    n = sig.samples.shape[0]
    rate = float(sig.rate_hz)
    if n == 0 or n / rate > 600:
        return None
    x = ma.mono(sig).astype(np.float64)
    frame = max(1, int(round(0.005 * rate)))
    m = len(x) // frame
    if m < 4:
        return None
    rms = np.sqrt(np.mean(x[:m * frame].reshape(m, frame) ** 2, axis=1))
    db = 20 * np.log10(np.maximum(rms, 1e-12))
    loud = db > max(db.max() - 35.0, -60.0)
    min_gap = max(1, int(round(gap_s / 0.005)))
    stretches, start, quiet = [], None, 0
    for k in range(m):
        if loud[k]:
            if start is None:
                start = k
            quiet = 0
            last = k
        elif start is not None:
            quiet += 1
            if quiet >= min_gap:
                stretches.append((start, last + 1))
                start, quiet = None, 0
    if start is not None:
        stretches.append((start, last + 1))
    stretches = [(a, b) for a, b in stretches if (b - a) * frame / rate >= 0.03]
    if not stretches or len(stretches) > 5000:
        return None
    win = max(64, int(round(0.04 * rate)))
    hop = max(16, win // 2)
    n_fft = 1 << int(math.ceil(math.log2(max(win * 8, 1024))))
    window = np.hanning(win)
    tones = []
    for a, b in stretches:
        seg = x[a * frame:b * frame]
        if len(seg) < win:
            seg = np.concatenate([seg, np.zeros(win - len(seg))])
        starts = list(range(0, len(seg) - win + 1, hop)) or [0]
        pitches = []
        for s0 in starts:
            spec = np.abs(np.fft.rfft(seg[s0:s0 + win] * window, n_fft))
            k = int(spec[1:].argmax()) + 1
            if 1 <= k < len(spec) - 1:
                y0, y1, y2 = np.log(spec[k - 1:k + 2] + 1e-12)
                d = 0.5 * (y0 - y2) / (y0 - 2 * y1 + y2) if (y0 - 2 * y1 + y2) != 0 else 0.0
            else:
                d = 0.0
            share = float((spec[max(0, k - 6):k + 7] ** 2).sum() / max((spec ** 2).sum(), 1e-24))
            pitches.append((float((k + d) * rate / n_fft), share))
        runs, cur = [], [0]
        for i in range(1, len(pitches)):
            ref = np.median([pitches[j][0] for j in cur])
            if abs(pitches[i][0] - ref) <= steady * ref:
                cur.append(i)
            else:
                runs.append(cur)
                cur = [i]
        runs.append(cur)
        steady_frames = sum(len(r) for r in runs if len(r) >= 2 or len(pitches) == 1)
        if steady_frames < 0.7 * len(pitches) or np.mean([p[1] for p in pitches]) < 0.5:
            return None
        for r in runs:
            if len(r) < 2 and len(pitches) > 1:
                continue
            t0 = a * frame / rate + starts[r[0]] / rate
            t1 = a * frame / rate + (starts[r[-1]] + win) / rate
            tones.append((round(t0, 4), round(min(t1, b * frame / rate), 4),
                          float(np.median([pitches[j][0] for j in r]))))
    return tones


def _mesh_quantities(mesh, unit: Unit | None, unit_how: str) -> list:
    from . import measure_mesh as mm
    out = [_q("count", "mesh.vertices", int(len(mesh.vertices)), nouns=("vertex", "point"), aggregate="count",
              primary=True, how="vertices"),
           _q("count", "mesh.faces", int(len(mesh.faces)), nouns=("face", "triangle", "facet"), aggregate="count",
              how="triangles")]
    b = mm.bounds(mesh)
    if b:
        lo, hi = [float(v) for v in b["min"]], [float(v) for v in b["max"]]
        size = tuple(h - l for l, h in zip(lo, hi))
        scale = max(1e-12, max(abs(v) for v in lo + hi))
        tol = REL_EPS * scale + 1e-12
        where = "smallest and largest coordinate per axis" + (f"; unit {unit.symbol} {unit_how}" if unit else
                                                              "; the file states no unit")
        out.append(_q("extent", "mesh.size", size, unit=unit, words=("size", "dimension", "extent", "bounding",
                                                                     "box"), tolerance=tol, how=where,
                      detail=(("ordered", False),)))
        out.append(_q("box", "mesh.box", {"x": (lo[0], hi[0]), "y": (lo[1], hi[1]), "z": (lo[2], hi[2])},
                      unit=unit, words=("bounding", "box", "extent"), tolerance=tol, how=where))
        if mesh.closed() and len(mesh.faces):
            vol = abs(mm.signed_volume(mesh))
            area = mm.surface_area(mesh)
            if unit is not None and unit.dim == "length":
                f = unit.factor
                out.append(_q("number", "mesh.volume", vol * f ** 3, unit=unit_of("mm3"), words=("volume",),
                              tolerance=REL_EPS * max(1.0, vol * f ** 3), how="enclosed volume (closed surface)"))
                out.append(_q("number", "mesh.area", area * f ** 2, unit=unit_of("mm2"), words=("area", "surface"),
                              tolerance=REL_EPS * max(1.0, area * f ** 2), how="surface area"))
    return out


def _graph_quantities(g) -> list:
    out = []
    kinds: dict[str, int] = {}
    for n in g.nodes:
        kinds[n.kind] = kinds.get(n.kind, 0) + 1
    for kind, count in sorted(kinds.items()):
        if kind == "unresolved":
            continue
        out.append(_q("count", f"graph.nodes.{kind}", count, nouns=(rr.singular(kind),), aggregate="count",
                      primary=kind == "function", how=f"nodes of kind {kind}"))
    label = {n.id: n.label for n in g.nodes}
    nkind = {n.id: n.kind for n in g.nodes}
    by_kind: dict[str, list] = {}
    for e in g.edges:
        by_kind.setdefault(e.kind, []).append(e)
    for kind, edges in sorted(by_kind.items()):
        out.append(_q("count", f"graph.edges.{kind}", len(edges), nouns=(rr.singular(kind),), aggregate="count",
                      how=f"edges of kind {kind}"))
        pairs = tuple(sorted({(label.get(e.src, e.src), label.get(e.dst, e.dst)) for e in edges}))
        defined = tuple(sorted({n.label for n in g.nodes if n.kind not in ("unresolved", "module")}))
        unresolved = tuple(sorted({n.label for n in g.nodes if n.kind == "unresolved"}))
        out.append(_q("pairs", f"graph.edges.{kind}.pairs", pairs, words=(rr.singular(kind), kind),
                      how=f"the {kind} edges the code states by name (read with ast, never run)",
                      detail=(("edge", kind), ("defined", defined), ("unresolved", unresolved),
                              ("kinds", tuple(sorted({nkind.get(e.src, "") for e in edges}))))))
    return out


def _tree_pairs(tree) -> list:
    """Name pairs a tree states: records with two string fields (caller/callee-like), and an object's string field
    against the same field of the objects in its array members (a call tree). Each key pair is one candidate."""
    from .types import _walk
    found: dict[tuple, list] = {}
    for node, _ in _walk(tree.root, limit=200_000):
        if node.attrs.get("type") == "array":
            recs = [c for c in node.children if c.attrs.get("type") == "object"]
            if len(recs) >= 2:
                keyset = None
                for r in recs:
                    ks = [c.name for c in r.children if isinstance(c.value, str)]
                    keyset = ks if keyset is None else [k for k in keyset if k in ks]
                for k1, k2 in itertools.combinations(keyset or [], 2):
                    pairs = []
                    for r in recs:
                        v = {c.name: c.value for c in r.children if isinstance(c.value, str)}
                        pairs.append((v[k1], v[k2]))
                    found.setdefault(("fields", k1, k2), []).extend(pairs)
        if node.attrs.get("type") == "object":
            fields = {c.name: c.value for c in node.children if isinstance(c.value, str)}
            for arr in (c for c in node.children if c.attrs.get("type") == "array"):
                for child in arr.children:
                    if child.attrs.get("type") != "object":
                        continue
                    cf = {c.name: c.value for c in child.children if isinstance(c.value, str)}
                    for k in fields:
                        if k in cf:
                            found.setdefault(("nested", k, arr.name), []).append((fields[k], cf[k]))
        if sum(len(v) for v in found.values()) > 100_000:
            break
    out = []
    for (how, k1, k2), pairs in found.items():
        if len(pairs) < 2:
            continue
        words = name_words(k1) + name_words(k2)
        text = (f"the values of fields '{clean(k1, 40)}' and '{clean(k2, 40)}' of each record" if how == "fields" else
                f"field '{clean(k1, 40)}' of an object and of the objects in its '{clean(k2, 40)}' list")
        out.append(_s("pairs", f"tree.{how}", tuple(pairs), words=words, how=text, detail=(("form", how),)))
    return out


def _chart_series(data: bytes) -> list:
    """A chart's series recovered through the reading contract (DESIGN section 6) by the independent reader in
    `check/svg_read.py`: axes fitted from ticks, marks mapped back through them. The <metadata> is never read."""
    from .check import svg_read as sv
    try:
        parsed = sv.parse(data)
    except Exception:                                  # a named Malformed/Refused, or a parser error: no chart
        return []
    root = parsed.root
    axes = sv.axes_of(root)
    ax, ay = axes.get("x"), axes.get("y")
    if ax is None or ay is None or ay.kind == "band" or len(ay.ticks) < 2 or len(ax.ticks) < 1:
        return []
    if ay.residual > 0.001 or (ax.kind != "band" and ax.residual > 0.001):
        return []
    title = " ".join(n.text for n in root.iter() if n.tag == "title" and n.parent is root)
    xkind = {"band": "category", "time": "time", "linear": "number", "log": "number"}[ax.kind]
    out = []
    plots = [n for n in root.iter() if n.tag == "g" and "plot" in n.classes()]
    for p in plots:
        for s in sv.plot_marks(p):
            pts = []
            pix = [pt for ln in s.lines for pt in ln] + list(s.points)
            for px, py in pix:
                x = ax.category_at(px) if ax.kind == "band" else ax.value(px)
                pts.append((x, ay.value(py)))
            tol0 = ay.tol()
            for x0, y0, x1, y1, _n in s.boxes:
                centre = (x0 + x1) / 2
                x = ax.category_at(centre) if ax.kind == "band" else ax.value(centre)
                vt, vb = ay.value(y0), ay.value(y1)
                v = vt if abs(vb) <= tol0 else vb if abs(vt) <= tol0 else vt
                pts.append((x, v))
            if not pts:
                continue
            words = name_words(s.name) or name_words(title)
            unit = _name_unit(s.name) or _name_unit(title)
            k = len(out)
            out.append(_s("series", f"chart.series[{k}]", tuple(pts), words=words, unit=unit,
                          how="marks read back through the axes fitted from the tick labels (reading contract)",
                          tolerance=ay.tol(),
                          detail=(("x_kind", xkind), ("log", ay.kind == "log"),
                                  ("x_tol", ax.tol() if ax.kind != "band" else 0.0), ("name", clean(s.name, 60)))))
            ys = [y for _, y in pts if isinstance(y, float) and math.isfinite(y)]
            if ys:
                values = {"max": max(ys), "min": min(ys), "first": ys[0], "last": ys[-1],
                          "mean": sum(ys) / len(ys), "sum": sum(ys)}
                for agg, v in values.items():
                    tol = ay.tol() * (len(ys) if agg == "sum" else 1)
                    if ay.kind == "log":
                        tol = abs(v) * (10 ** (ay.tol() * (len(ys) if agg == "sum" else 1)) - 1)
                    out.append(_s("number", f"chart.series[{k}].{agg}", v, unit=unit, words=words, aggregate=agg,
                                  tolerance=tol, how=f"{agg} of the values the chart shows (read back from its marks)",
                                  detail=(("derived", True),)))
    return out


def _name_unit(name: str | None) -> Unit | None:
    """The unit a name carries in parentheses or brackets: "Rainfall (mm)", "Price [EUR]"."""
    m = re.search(r"[(\[]\s*([^()\[\]]{1,16}?)\s*[)\]]\s*$", name or "")
    return unit_of(m.group(1)) if m else None


def _document_facts(doc, *, base_unit=None) -> list:
    from .types import Table
    out = []
    counts: dict[str, int] = {}
    for b in doc.blocks:
        counts[b.kind] = counts.get(b.kind, 0) + 1
    nouns = {"heading": ("heading", "section"), "paragraph": ("paragraph",), "list_item": ("item", "point", "step"),
             "table": ("table",), "figure": ("figure", "image", "picture"), "page_break": ("page",)}
    for kind, n in sorted(counts.items()):
        if kind in nouns:
            out.append(_q("count", f"document.{kind}", n, nouns=nouns[kind], aggregate="count", how=f"{kind} blocks"))
    text = doc.text()
    events = rr.events_of_text(text)
    if events is not None:
        out.append(_s("events", "document.events", tuple(events), nouns=("event", "entry", "line"),
                      how="lines that carry a timestamp (ISO 8601, syslog, common log format or a clock time)"))
        out.append(_q("count", "document.events.count", len(events), nouns=("event", "entry"), aggregate="count",
                      how="lines with a timestamp"))
        return out                                   # a log's numbers are not statements of quantities
    where = 0
    run: list[str] = []
    for i, b in enumerate(doc.blocks):
        if b.kind == "list_item":
            run.append(b.text)
            continue
        if len(run) >= 2:
            out.append(_s("sequence", f"document.list@{i}", rr.Sequence(run, "list", i), nouns=SEQUENCE_NOUNS,
                          how="list items in order"))
        run = []
        if b.kind == "table" and isinstance(b.ref, Table):
            out += _table_quantities(b.ref, f"document.table@{i}", Statement)
            continue
        if b.kind in ("paragraph", "note") and b.text.count("\n") >= 1:
            lines = [ln for ln in b.text.splitlines() if ln.strip()]
            if len(lines) >= 2:
                out.append(_s("sequence", f"document.lines@{i}", rr.Sequence(lines, "lines", i),
                              nouns=SEQUENCE_NOUNS, how="the lines of one paragraph in order"))
        elif b.kind in ("paragraph", "note"):
            said = [t for _, t in rr.sentences(b.text)]
            if len(said) >= 2:
                out.append(_s("sequence", f"document.sentences@{i}", rr.Sequence(said, "sentences", i),
                              nouns=SEQUENCE_NOUNS, how="the sentences of one paragraph in order"))
    if len(run) >= 2:
        out.append(_s("sequence", "document.list@end", rr.Sequence(run, "list", len(doc.blocks)),
                      nouns=SEQUENCE_NOUNS, how="list items in order"))
    texts = [b.text for b in doc.blocks if b.kind in ("heading", "paragraph", "list_item", "note", "figure", "code")
             and b.text]
    for said in rr.statements_of_text("\n".join(texts), default_unit=base_unit):
        out.append(_said(said, "document"))
        where += 1
    return out


def _said(said, prefix: str) -> Statement:
    detail = (("approx", said.approx), ("bound", said.bound), ("alternatives", said.alternatives),
              ("axes", said.axes))
    return _s(said.kind, f"{prefix}.sentence[{said.where}]", said.value, unit=said.unit, words=said.words,
              aggregate=said.aggregate, nouns=(said.noun,) if said.noun else (), tolerance=said.tolerance,
              sentence=said.sentence, how=f"stated in sentence {said.where + 1}", detail=detail)


_STATED_KINDS = ("events", "sequence")


def quantities(representation, *, unit: Unit | None = None, unit_how: str = "", label_words=()) -> list:
    """What can be MEASURED from a decoded representation, each value with how it was computed."""
    r = representation
    kind = getattr(r, "kind", None)
    if kind == "table":
        return [f for f in _table_quantities(r, "table", Quantity, label_words) if f.kind not in _STATED_KINDS]
    if kind == "signal":
        return _signal_quantities(r)
    if kind == "mesh":
        return _mesh_quantities(r, unit or unit_of(r.unit), unit_how or ("as the decoder read it" if r.unit else ""))
    if kind == "graph":
        return _graph_quantities(r)
    if kind == "raster":
        h, w = r.pixels.shape[:2]
        return [_q("extent", "raster.size", (float(w), float(h)), unit=unit_of("px"),
                   words=("size", "dimension", "resolution", "width", "height", "image", "picture"),
                   how="decoded width x height in pixels", detail=(("ordered", True),)),
                _q("count", "raster.pixels", int(w * h), nouns=("pixel",), aggregate="count", how="width x height")]
    if kind == "frames":
        out = [_q("count", "frames.frames", len(r.frames), nouns=("frame",), aggregate="count", primary=True,
                  how="frames"),
               _q("number", "frames.duration", float(sum(r.delays_s)), unit=unit_of("s"),
                  words=("duration", "length", "long", "last", "loop"), how="sum of the frame delays",
                  tolerance=0.0005 * len(r.frames))]
        if r.frames:
            h, w = r.frames[0].pixels.shape[:2]
            out.append(_q("extent", "frames.size", (float(w), float(h)), unit=unit_of("px"),
                          words=("size", "dimension", "resolution"), how="first frame width x height",
                          detail=(("ordered", True),)))
        return out
    if kind == "drawing":
        return [_q("extent", "drawing.size", (float(r.width), float(r.height)), unit=None,
                   words=("size", "dimension", "width", "height"), how="declared width x height (user units)",
                   detail=(("ordered", True),)),
                _q("count", "drawing.shapes", len(r.shapes), nouns=("shape", "element"), aggregate="count",
                   how="shapes")]
    if kind == "features":
        from . import measure_geo as mg
        out = [_q("count", "features.features", len(r.features), nouns=("feature",), aggregate="count", primary=True,
                  how="features")]
        for geom, n in sorted(mg.counts(r).get("by_geometry", {}).items()) if isinstance(
                mg.counts(r).get("by_geometry"), dict) else []:
            out.append(_q("count", f"features.{geom}", n, nouns=(rr.singular(geom.lower()),), aggregate="count",
                          how=f"features of geometry {geom}"))
        waypoints = sum(1 for f in r.features if f.properties.get("_kind") == "waypoint")
        if waypoints:
            # lane FX3 (class X-H): a GPX waypoint is a named place on the route (a stop, a point of interest)
            out.append(_q("count", "features.waypoints", waypoints, nouns=("waypoint", "stop"), aggregate="count",
                          how="GPX waypoints (wpt elements)"))
        length = mg.lengths_m(r).get("total_m")
        if length is not None:
            out.append(_q("number", "features.length", float(length) * 1000.0, unit=unit_of("mm"),
                          words=("length", "distance", "long", "route", "track"), tolerance=1e-6 * length * 1000,
                          how="haversine length of every line, mean Earth radius"))
        span = mg.time_span(r)
        if span:
            out.append(_q("number", "features.duration", float(span["duration_s"]), unit=unit_of("s"),
                          words=("duration", "took", "time", "long"), how="latest minus earliest timestamp",
                          tolerance=1.0))
            times = tuple(sorted(t for f in r.features for t in f.properties.get("times_s", ()) if
                                 not math.isnan(t)))[:rr.MAX_EVENTS]
            out.append(_q("times", "features.times", times, unit=unit_of("s"), how="the points' timestamps",
                          detail=(("absolute", True),)))
        return out
    if kind == "bundle":
        return [_q("count", "bundle.members", len(r.members), nouns=("file", "member", "entry", "document"),
                   aggregate="count", primary=True, how="members listed"),
                _q("number", "bundle.size", float(sum(m.size for m in r.members)), unit=unit_of("bytes"),
                   words=("size", "total"), aggregate="sum", how="sum of the members' sizes")]
    if kind == "document":
        return [f for f in _document_facts(r) if isinstance(f, Quantity)]
    if kind == "tree":
        out = [_q("count", "tree.nodes", sum(1 for _ in _tree_walk(r)), nouns=("node", "element"),
                  aggregate="count", how="nodes")]
        from .adapters import jsontree
        try:
            t = jsontree.as_table(r)
        except Exception:
            t = None
        if t is not None:
            out += [f for f in _table_quantities(t, "tree.records", Quantity, label_words)
                    if f.kind not in _STATED_KINDS]
        return out
    if kind == "opaque":
        return [_q("number", "opaque.size", float(r.size), unit=unit_of("bytes"), words=("size",),
                   how="bytes; nothing could be decoded")]
    return []


def _tree_walk(tree):
    from .types import _walk
    return (n for n, _ in _walk(tree.root, limit=200_000))


_COMMENT = re.compile(r"^\s*(?:#|//|;|%|comment\s)\s?(.*)$", re.I)


def own_comments(data: bytes | None, limit: int = 64 << 10) -> str:
    """The comment lines of a text file (#, //, ;, %, PLY 'comment'): the file's own words that a representation of
    its numbers does not keep (an OBJ's "# units: centimetres"). Empty for binary data."""
    if not data:
        return ""
    head = data[:limit]
    if b"\x00" in head:
        return ""
    try:
        text = head.decode("utf-8")
    except UnicodeDecodeError:
        return ""
    out = []
    for line in text.splitlines()[:2000]:
        m = _COMMENT.match(line)
        if m and m.group(1).strip():
            out.append(m.group(1).strip())
    return "\n".join(out)


def statements(representation, *, data: bytes | None = None) -> list:
    """What an artifact STATES in its own words or SHOWS by its marks."""
    r = representation
    kind = getattr(r, "kind", None)
    if kind == "document":
        return [f for f in _document_facts(r) if isinstance(f, Statement)]
    if kind == "drawing" and data:
        return _chart_series(data)
    if kind == "tree":
        out = _tree_pairs(r)
        from .adapters import jsontree
        try:
            t = jsontree.as_table(r)
        except Exception:
            t = None
        if t is not None:
            out += [f for f in _table_quantities(t, "tree.records", Statement) if f.kind in ("events", "sequence")]
        return out
    if kind == "table":
        return [f for f in _table_quantities(r, "table", Statement) if f.kind in ("events", "sequence")]
    if kind in ("bundle", "raster", "frames", "signal"):
        return []           # an image or a sound gives measurements only; what it depicts or says is never read
    comments = own_comments(data)
    if comments:
        return [_said(s, "comment") for s in rr.statements_of_text(comments)]
    return []


# ---- one side of a comparison ------------------------------------------------------------------------------------------

@dataclass
class Side:
    """One artifact ready to be compared: its facts, the names it carries and its own words (untrusted)."""
    name: str | None
    kind: str
    facts: list
    names: dict                     # normalised name -> shown name
    words: str                      # the artifact's own words, for mentions (untrusted)
    notes: list = field(default_factory=list)
    rep: object = field(default=None, repr=False)       # the representation (lane FX3: structure across sides)
    raw: bytes | None = field(default=None, repr=False)  # the file's bytes (lane FX3: an archive's member digests)
    fmt: str = ""                   # the decoder's format name (lane FX4: which lines a reader sees)
    source_notes: tuple = ()        # the decoder's notes (lane FX4: hidden elements a reader dropped)
    partial: bool = False           # the decoder read only part of it (lane FX5: an archive's listing is then unread)


def side(source, *, name: str | None = None, data: bytes | None = None) -> Side:
    """A Side from a Decoded (or a bare representation), its file name and its bytes (the bytes let a chart be read
    through the reading contract and a text file's comments be read)."""
    rep = getattr(source, "representation", source)
    kind = getattr(rep, "kind", "unknown")
    notes = []
    label_words = name_words(name) if name else ()
    said = statements(rep, data=data)
    declared = next((f.unit for f in said if f.kind == "unit" and f.unit and f.unit.dim == "length"), None)
    qs = quantities(rep, unit=declared if kind == "mesh" else None,
                    unit_how="stated in the file's own comment" if declared else "", label_words=label_words)
    if kind == "mesh" and declared:
        notes.append(f"the mesh's unit ({declared.symbol}) is the one its own comment states")
    facts = (qs + said)[:MAX_FACTS]
    detected = getattr(source, "detected", None)
    return Side(name, kind, facts, _names(rep, name, facts), _own_words(rep, data), notes, rep, data,
                str(getattr(detected, "format", "") or ""), tuple(getattr(source, "notes", ()) or ()),
                bool(getattr(source, "partial", False)))


def _own_words(rep, data) -> str:
    try:
        text = rep.text() if hasattr(rep, "text") else ""
    except Exception:
        text = ""
    return ((text or "") + "\n" + own_comments(data))[:rr.MAX_TEXT]


def _norm_name(text: str) -> str:
    return re.sub(r"\s+", " ", str(text)).strip().casefold()


def _names(rep, name, facts) -> dict:
    out: dict[str, str] = {}

    def add(text):
        t = clean(str(text), 80)
        k = _norm_name(t)
        words = name_words(t)
        if len(k) >= 3 and not rr.is_number_like(k) and words and not set(words) <= GENERIC_NAMES \
                and len(out) < 2000:
            out.setdefault(k, t)
    kind = getattr(rep, "kind", "")
    if kind == "table":
        for c in rep.columns:
            add(c.name)
    elif kind == "document":
        for b in rep.blocks:
            if b.kind == "heading":
                add(b.text)
    elif kind == "graph":
        for n in rep.nodes:
            if n.kind not in ("module", "unresolved"):
                add(n.label)
    elif kind == "bundle":
        for m in rep.members:
            add(m.path.rsplit("/", 1)[-1])
    for f in facts:
        if f.kind == "series" and f.get("name"):
            add(f.get("name"))
    return out


# ---- comparing -----------------------------------------------------------------------------------------------------------

@dataclass
class Pair:
    verdict: str                    # consistent | inconsistent | not_comparable
    a: Fact                         # from the first artifact
    b: Fact                         # from the second
    a_value: object
    b_value: object
    tolerance: float | None
    strong: bool
    how: str                        # plain words: what was compared and how
    note: str = ""
    focus: int = 0
    unit: str | None = None
    conn: int = 0                   # for a number: how strongly the statement names the quantity
    name_only: tuple = ()           # for a count joined only by a file's or table's name: the joining words
    complete: bool = False          # lane FX3: a structure comparison that used every line of its document
    measures: frozenset = frozenset()  # lane FX4: the quantities a structure comparison measures (QUANTITY_WORDS)
    read: object = None             # lane FX5: what the comparison's closed grammar read of the question (C1)

    def basis(self) -> str:
        tol = "none" if self.tolerance is None else f"{self.tolerance:.6g}"
        ref = (self.a.ref + "/" + self.b.ref).replace("[", ":").replace("]", "")
        return f"{ref};tol={tol}"


def _connection(stmt: Fact, q: Fact) -> int:
    """How strongly a number statement names a quantity: shared words, and a unit both state."""
    score = len(set(stmt.words) & set(q.words))
    if stmt.unit is not None and q.unit is not None and stmt.unit.dim == q.unit.dim:
        score += 1 if not stmt.unit.dim.startswith("money") else 2
    return score


def _close(a: float, b: float, tol: float) -> bool:
    return abs(a - b) <= tol + REL_EPS * max(1.0, abs(a), abs(b))


def _number_verdict(stmt: Fact, q: Fact, sv: float, qv: float, tol: float) -> tuple[str, str]:
    bound = stmt.get("bound")
    if bound == "lower":
        return ("consistent" if qv > sv - tol else "inconsistent"), "the statement is a lower bound"
    if bound == "upper":
        return ("consistent" if qv < sv + tol else "inconsistent"), "the statement is an upper bound"
    if _close(sv, qv, tol):
        return "consistent", ""
    for alt in stmt.get("alternatives") or ():
        if _close(alt, qv, tol):
            return "consistent", "read with the other decimal convention the text allows"
    return "inconsistent", ""


def _fmt(v) -> str:
    if isinstance(v, float):
        return f"{v:.6g}" if abs(v) < 1e15 else f"{v:.3e}"
    if isinstance(v, tuple) and len(v) <= 6 and all(isinstance(x, (int, float)) for x in v):
        return " x ".join(_fmt(float(x)) for x in v)
    return str(v)


_SCALES = (10.0, 100.0, 1000.0, 1e4, 1e5, 1e6, 60.0, 3600.0, 86400.0, 1024.0, 1048576.0, 25.4, 2.54, 12.0, 16.0)


def _scaled(a: float, b: float, tol: float) -> bool:
    """Whether a common unit factor maps one value onto the other (within the comparison's tolerance, scaled)."""
    for f in _SCALES:
        if abs(a * f - b) <= tol * f or abs(b * f - a) <= tol * f:
            return True
    return False


def _yearlike(f: Fact) -> bool:
    v = f.value
    return f.unit is None and isinstance(v, float) and v.is_integer() and 1800 <= v <= 2200


def _cmp_number(fa: Fact, fb: Fact, q_aggs, explicit_sentences) -> Pair | None:
    if not rr.compatible(fa.unit, fb.unit):
        return None
    stmt, meas = (fa, fb) if isinstance(fa, Statement) or not isinstance(fb, Statement) else (fb, fa)
    if (fa.get("derived") and isinstance(fb, Quantity)) or (fb.get("derived") and isinstance(fa, Quantity)):
        return None                       # a chart's aggregates meet a table through its series, not twice
    if fa.get("derived") and fb.get("derived"):
        return None
    if isinstance(fa, Statement) and isinstance(fb, Statement) and fb.get("derived"):
        stmt, meas = fa, fb               # a document's words against what a chart shows
    elif isinstance(fa, Statement) and isinstance(fb, Statement) and fa.get("derived"):
        stmt, meas = fb, fa
    agg_s, agg_m = stmt.aggregate, meas.aggregate
    note = ""
    conn = _connection(stmt, meas)
    if agg_s != agg_m:
        if agg_s is None and agg_m is not None and len(q_aggs) == 1 and agg_m == q_aggs[0] and conn > 0 \
                and stmt.ref not in explicit_sentences and not _yearlike(stmt):
            note = "the aggregate is the one the question names"
        else:
            return None
    unit = stmt.unit or meas.unit
    sv = rr.convert(float(stmt.value), stmt.unit, unit) if stmt.unit else float(stmt.value)
    mv = rr.convert(float(meas.value), meas.unit, unit) if meas.unit else float(meas.value)
    tol = max(stmt.tolerance * (stmt.unit.factor / unit.factor if stmt.unit and unit else 1.0), meas.tolerance)
    verdict, extra = _number_verdict(stmt, meas, sv, mv, tol)
    if verdict == "inconsistent" and (stmt.unit is None) != (meas.unit is None) and _scaled(sv, mv, tol):
        # lane FX, item 3: one side states a unit and the other states none; a unit's scale factor would make them
        # agree (1.025 kg against a column summing to 1025): which unit the bare numbers are in is not known, so
        # this is not a disagreement (as for a mesh without a unit, ADR-077)
        verdict = "not_comparable"
        extra = "; ".join(x for x in (extra, "one side states no unit, and a unit's scale factor would make them "
                                      "agree") if x)
    if agg_m is None:                         # a scalar (a duration): its words must be shared, beyond the unit
        strong = bool(set(stmt.words) & set(meas.words))
        named = strong
    else:                                     # an aggregate: stated or named by the question; tied to its subject
        strong = conn > 0 or agg_s is not None
        named = conn > 0
    a_val, b_val = (sv, mv) if stmt is fa else (mv, sv)
    how = (f"{agg_m or 'value'} compared within {_fmt(tol)}" + (f" {unit.symbol}" if unit else ""))
    p = Pair(verdict, fa, fb, a_val, b_val, tol, strong, how, "; ".join(x for x in (note, extra) if x),
             unit=unit.symbol if unit else None)
    p.conn = conn if named else 0
    return p


def _cmp_count(fa: Fact, fb: Fact) -> Pair | None:
    """Counts of the same thing: the nouns agree. A file's or a table's name is weak evidence of what its rows are
    ("14 receipts" for receipts.csv may count its rows, but calls.csv may as well hold one row per day with a column
    of calls): a pair joined only by such a name is listed and never decides by itself (it is weak, `strong` False),
    so a row count never becomes a named quantity on the strength of a name alone."""
    own_a, own_b = set(fa.nouns), set(fb.nouns)
    na = own_a | set(fa.get("also_nouns") or ())
    nb = own_b | set(fb.get("also_nouns") or ())
    if not (na & nb):
        return None
    pair = _count_pair(fa, fb, "counts of the same thing (" + sorted(na & nb)[0] + ")")
    if not (own_a & own_b):
        pair.strong = False
        pair.name_only = tuple(sorted(na & nb))
        pair.note = "; ".join(x for x in (pair.note, "joined only by a file's or table's name, which is weak "
                                          "evidence of what its rows are") if x)
    return pair


def _count_pair(fa, fb, how, note="") -> Pair:
    va, vb = int(fa.value), int(fb.value)
    stmt = fa if isinstance(fa, Statement) else fb if isinstance(fb, Statement) else None
    if stmt is not None and stmt.get("bound") in ("lower", "upper"):
        other = vb if stmt is fa else va
        sv = int(stmt.value)
        ok = other > sv if stmt.get("bound") == "lower" else other < sv
    elif stmt is not None and stmt.get("approx"):
        ok = abs(va - vb) <= max(1, 0.05 * max(va, vb))
    else:
        ok = va == vb
    return Pair("consistent" if ok else "inconsistent", fa, fb, va, vb, 0.0, True, how, note)


def _match_multiset(stated, measured, tol) -> bool:
    for combo in itertools.permutations(range(len(measured)), len(stated)):
        if all(_close(stated[i], measured[j], tol) for i, j in enumerate(combo)):
            return True
    return False


def _cmp_extent(fa: Fact, fb: Fact) -> Pair | None:
    stmt, meas = (fa, fb) if isinstance(fa, Statement) else (fb, fa)
    if not isinstance(stmt, Statement) or isinstance(meas, Statement):
        return None
    sv, mv = tuple(stmt.value), tuple(meas.value)
    axes = tuple(stmt.get("axes") or ())
    pixels_both = meas.unit is not None and meas.unit.dim == "pixels" and (stmt.unit is None or stmt.unit.dim ==
                                                                          "pixels")
    if meas.get("ordered") and axes and len(axes) == len(sv) and set(axes) <= {"width", "height"} \
            and len(set(axes)) == len(axes) and pixels_both:
        # lane FX3 (class X-F): lengths tied to an axis by their word ("120 pixels wide", "210 px tall") against
        # the measured width x height, axis by axis
        picked = tuple(mv[{"width": 0, "height": 1}[a]] for a in axes)
        tol = stmt.tolerance
        ok = all(_close(x, y, tol) for x, y in zip(sv, picked))
        a_val, b_val = (sv, picked) if stmt is fa else (picked, sv)
        return Pair("consistent" if ok else "inconsistent", fa, fb, a_val, b_val, tol, True,
                    " and ".join(axes) + " as stated, against the measured size",
                    unit=(meas.unit or stmt.unit).symbol if (meas.unit or stmt.unit) else None)
    if not 2 <= len(sv) <= len(mv):
        return None
    if stmt.unit is not None and meas.unit is not None and stmt.unit.dim != meas.unit.dim:
        return None
    if meas.get("ordered") and (stmt.unit is None or pixels_both) and len(sv) == len(mv):
        # lane FX4 (R4): pixels stated are compared only with pixels measured (a drawing's size is in its own
        # units, which may be millimetres)
        tol = stmt.tolerance
        ok = all(_close(s, m, tol) for s, m in zip(sv, mv))
        return Pair("consistent" if ok else "inconsistent", fa, fb, sv if stmt is fa else mv, mv if stmt is fa
                    else sv, tol, True, "width and height, in that order", unit=(meas.unit or stmt.unit).symbol
                    if (meas.unit or stmt.unit) else None)
    if meas.get("ordered"):
        return None
    note = ""
    unit = stmt.unit
    if stmt.unit is not None and meas.unit is not None:
        mv = tuple(rr.convert(v, meas.unit, stmt.unit) for v in mv)
    elif stmt.unit is not None and meas.unit is None:
        note = "the measured artifact states no unit: compared as if in the stated unit"
    tol = stmt.tolerance + meas.tolerance
    ok = _match_multiset(sv, mv, tol)
    if not ok and note:
        ratios = [m / s for s, m in zip(sorted(sv), sorted(mv)) if s]
        if ratios and all(abs(r - ratios[0]) <= 0.002 * abs(ratios[0]) for r in ratios):
            return Pair("not_comparable", fa, fb, sv, mv, tol, True, "sizes, axes not stated",
                        "the sizes agree only after one scale factor; the measured artifact states no unit")
    how = "sizes compared as a set (the statement does not tie them to axes)"
    a_val, b_val = (sv, mv) if stmt is fa else (mv, sv)
    return Pair("consistent" if ok else "inconsistent", fa, fb, a_val, b_val, tol, True, how, note,
                unit=unit.symbol if unit else None)


def _cmp_box(fa: Fact, fb: Fact) -> Pair | None:
    stmt, meas = (fa, fb) if isinstance(fa, Statement) else (fb, fa)
    if not isinstance(stmt, Statement) or isinstance(meas, Statement):
        return None
    if stmt.unit is not None and meas.unit is not None and stmt.unit.dim != meas.unit.dim:
        return None
    axes = [a for a in stmt.value if a in meas.value]
    if len(axes) < 2:
        return None
    note = "" if meas.unit is not None or stmt.unit is None else \
        "the measured artifact states no unit: compared as if in the stated unit"
    conv = (lambda v: rr.convert(v, meas.unit, stmt.unit)) if stmt.unit and meas.unit else (lambda v: v)
    tol = stmt.tolerance + meas.tolerance
    bad = [a for a in axes if not (_close(stmt.value[a][0], conv(meas.value[a][0]), tol) and
                                   _close(stmt.value[a][1], conv(meas.value[a][1]), tol))]
    sv = {a: stmt.value[a] for a in axes}
    mv = {a: (conv(meas.value[a][0]), conv(meas.value[a][1])) for a in axes}
    how = "smallest and largest coordinate on each named axis"
    if bad:
        how += " (differs on " + ", ".join(bad) + ")"
    return Pair("inconsistent" if bad else "consistent", fa, fb, sv if stmt is fa else mv, mv if stmt is fa else sv,
                tol, True, how, note, unit=(stmt.unit or meas.unit).symbol if (stmt.unit or meas.unit) else None)


def _cmp_range(fa: Fact, fb: Fact) -> Pair | None:
    if not rr.compatible(fa.unit, fb.unit):
        return None
    stmt, meas = (fa, fb) if isinstance(fa, Statement) else (fb, fa)
    if not isinstance(stmt, Statement) or isinstance(meas, Statement) or _connection(stmt, meas) <= 0:
        return None
    unit = stmt.unit or meas.unit
    mv = tuple(rr.convert(v, meas.unit, unit) for v in meas.value) if meas.unit and unit else meas.value
    tol = stmt.tolerance + meas.tolerance
    ok = _close(stmt.value[0], mv[0], tol) and _close(stmt.value[1], mv[1], tol)
    return Pair("consistent" if ok else "inconsistent", fa, fb, stmt.value if stmt is fa else mv,
                mv if stmt is fa else stmt.value, tol, True, "smallest and largest value", unit=unit.symbol
                if unit else None)


def _x_key(x, kind):
    return str(x).strip().casefold() if kind == "category" else float(x)


def _cmp_series(fa: Fact, fb: Fact, single: bool) -> Pair | None:
    """A shown series against a measured one: aligned by x (labels, numbers or times), values within the reading's
    tolerance; or, when one is a sound's pitches, related by one straight line (pitch linear in the value)."""
    named = bool(set(fa.words) & set(fb.words))
    if not named and not single:
        return None
    pa, pb = list(fa.value), list(fb.value)
    pitch = [f for f in (fa, fb) if f.unit is not None and f.unit.dim == "frequency" and f.ref == "signal.pitches"]
    if pitch:
        if not named and not single:
            return None
        if len(pa) != len(pb) or len(pa) < 3:
            return Pair("not_comparable", fa, fb, len(pa), len(pb), None, named, "pitch against values",
                        "different numbers of tones and values: no one-to-one reading")
        p, other = (pa, pb) if fa in pitch else (pb, pa)
        x = np.array([v for _, v in other], dtype=np.float64)
        y = np.array([v for _, v in p], dtype=np.float64)
        if np.ptp(x) == 0 or np.ptp(y) == 0:
            return None
        b1, b0 = np.polyfit(x, y, 1)
        resid = float(np.max(np.abs(b0 + b1 * x - y)))
        tol = max(pitch[0].tolerance, 0.02 * float(np.ptp(y)))
        ok = b1 > 0 and resid <= tol
        return Pair("consistent" if ok else "inconsistent", fa, fb, len(pa), len(pb), tol, named or single,
                    "each tone's pitch against the value in the same position, one straight line through all",
                    f"largest misfit {resid:.3g} Hz")
    if not rr.compatible(fa.unit, fb.unit):
        return None
    ka, kb = fa.get("x_kind"), fb.get("x_kind")
    shown, meas = (fa, fb) if isinstance(fa, Statement) else (fb, fa)
    tol_y = max(fa.tolerance, fb.tolerance)
    log = shown.get("log") or meas.get("log")
    tol_x = max(fa.get("x_tol") or 0.0, fb.get("x_tol") or 0.0)

    def close_y(u, v):
        if log:
            return u > 0 and v > 0 and abs(math.log10(u) - math.log10(v)) <= tol_y + 1e-12
        return _close(u, v, tol_y)
    ps, pm = list(shown.value), list(meas.value)
    if len(ps) > MAX_SERIES or len(pm) > MAX_SERIES:
        return Pair("not_comparable", fa, fb, len(ps), len(pm), None, False, "values one by one",
                    f"more than {MAX_SERIES} values: not compared point by point here")
    aligned = ka == kb and ka in ("category", "number", "time")
    if not aligned and {ka, kb} <= {"number", "index"}:
        aligned = False
    pairs_xy, stray_s, stray_m = [], [], []
    if aligned:
        used = [False] * len(ps)
        for x, y in pm:
            best = None
            for j, (xs, ys) in enumerate(ps):
                if used[j]:
                    continue
                if ka == "category":
                    if _x_key(xs, ka) == _x_key(x, ka):
                        best = j
                        break
                elif abs(float(xs) - float(x)) <= max(tol_x, 1e-9 * max(1.0, abs(float(x)))):
                    if best is None or abs(float(ps[best][0]) - float(x)) > abs(float(xs) - float(x)):
                        best = j
            if best is None:
                stray_m.append((x, y))
            else:
                used[best] = True
                pairs_xy.append((x, ps[best][1], y))
        stray_s = [ps[j] for j in range(len(ps)) if not used[j]]
        how = "each value matched to the mark at the same x, within the reading's tolerance"
        if not pairs_xy:
            aligned = False
    if not aligned:
        if len(ps) != len(pm):
            return Pair("inconsistent" if named else "not_comparable", fa, fb, len(ps), len(pm), None, named,
                        "number of values", "the x positions cannot be matched; the counts differ")
        order = sorted(range(len(ps)), key=lambda j: float(ps[j][0]) if ka != "category" else j)
        pairs_xy = [(pm[i][0], ps[order[i]][1], pm[i][1]) for i in range(len(pm))]
        how = "values matched by order (the x positions cannot be matched)"
    bad = [(x, s, m) for x, s, m in pairs_xy if not close_y(s, m)]
    ok = not bad and not stray_s and not stray_m
    note = []
    if bad:
        x, s, m = bad[0]
        note.append(f"{len(bad)} value(s) differ, first at x={_fmt(x) if not isinstance(x, str) else clean(x, 30)}: "
                    f"{_fmt(float(s))} shown against {_fmt(float(m))}")
    if stray_m:
        note.append(f"{len(stray_m)} value(s) have no mark")
    if stray_s:
        note.append(f"{len(stray_s)} mark(s) have no value")
    a_val, b_val = (len(ps), len(pm)) if shown is fa else (len(pm), len(ps))
    return Pair("consistent" if ok else "inconsistent", fa, fb, a_val, b_val, tol_y, named or single,
                how + (" (log axis: tolerance in decades)" if log else ""), "; ".join(note))


def _match_steps(steps: list[tuple], targets: list[tuple]) -> list:
    """Greedy one-to-one matching of steps to target lines by the share of each step's words found in the line;
    a step whose best line is tied with another is left unmatched (ambiguous). Bounded: past MAX_MATCH tries the
    remaining steps are left unmatched (so the comparison says it could not cover them)."""
    cand, n_steps = [], len(steps)
    if len(steps) * max(1, len(targets)) > MAX_MATCH:
        steps = steps[:max(1, MAX_MATCH // max(1, len(targets)))]
    for i, sw in enumerate(steps):
        if not sw:
            continue
        scores = []
        for j, tw in enumerate(targets):
            share = len(set(sw) & set(tw)) / len(sw)
            if share >= MATCH_SHARE:
                exact = len(set(sw) & set(tw)) / max(1, len(set(sw) | set(tw)))
                scores.append((share, exact, j))
        scores.sort(reverse=True)
        if len(scores) >= 2 and scores[0][:2] == scores[1][:2]:
            continue
        for share, exact, j in scores:
            cand.append((share, exact, -i, i, j))
    cand.sort(reverse=True)
    taken_s, taken_t, out = set(), set(), {}
    for share, exact, _, i, j in cand:
        if i in taken_s or j in taken_t:
            continue
        taken_s.add(i)
        taken_t.add(j)
        out[i] = j
    return [out.get(i) for i in range(n_steps)]


def _cmp_sequence_events(seq_fact: Fact, ev_fact: Fact) -> Pair | None:
    seq, events = seq_fact.value, list(ev_fact.value)
    match = _match_steps(seq.words, [e.words for e in events])
    found = [(i, events[j]) for i, j in enumerate(match) if j is not None]
    if len(found) < 2:
        return None
    # a step that shares not even a third of its words with any line is not about the log (a heading, an
    # introduction); a step that half-matches lines but none uniquely is a missing step
    unrelated = sum(1 for i, j in enumerate(match) if j is None and max(
        (len(set(seq.words[i]) & set(e.words)) / max(1, len(seq.words[i])) for e in events), default=0.0) < 1 / 3)
    steps = len(seq.items) - unrelated
    strong = len(found) * 3 >= 2 * steps
    inversions = []
    for (i1, e1), (i2, e2) in zip(found, found[1:]):
        if e2.seconds < e1.seconds - 1e-9:
            inversions.append((i1, i2, e1, e2))
    ties = sum(1 for (_, e1), (_, e2) in zip(found, found[1:]) if abs(e2.seconds - e1.seconds) <= 1e-9)
    missing = steps - len(found)
    note = [f"{unrelated} line(s) of the document share almost no words with the log and are not taken as "
            "steps"] if unrelated else []
    if inversions:
        i1, i2, e1, e2 = inversions[0]
        note.append(f"step {i1 + 1} is listed before step {i2 + 1}, but the log has step {i2 + 1} first "
                    f"({_clock(e2)} before {_clock(e1)})")
        verdict = "inconsistent"
    elif missing:
        verdict = "not_comparable"
        note.append(f"{missing} of {steps} steps were not found in the log")
    elif ties:
        verdict = "not_comparable"
        note.append(f"{ties} pair(s) of steps share one timestamp: their order is not established")
    else:
        verdict = "consistent"
    return Pair(verdict, seq_fact, ev_fact, len(seq.items), len(found), 0.0, strong,
                f"steps matched to log lines by their words ({len(found)} of {len(seq.items)}), order compared "
                "with the lines' timestamps", "; ".join(note))


def _clock(e) -> str:
    if e.absolute:
        from datetime import datetime, timezone
        return datetime.fromtimestamp(e.seconds, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    s = int(e.seconds) % 86400
    return f"{s // 3600:02d}:{s // 60 % 60:02d}:{s % 60:02d}"


def _cmp_events(fa: Fact, fb: Fact) -> Pair | None:
    """Two event lists (two logs): events matched by their words; order agreement and the offset between them."""
    ea, eb = list(fa.value), list(fb.value)
    match = _match_steps([e.words for e in rr.without_format_words(ea)], [e.words for e in eb])
    found = [(ea[i], eb[j]) for i, j in enumerate(match) if j is not None]
    if len(found) < 2:
        return None
    order_a = [x.seconds for x, _ in found]
    order_b = [y.seconds for _, y in found]
    ia = sorted(range(len(found)), key=lambda k: order_a[k])
    inverted = any(order_b[ia[k + 1]] < order_b[ia[k]] - 1e-9 for k in range(len(ia) - 1))
    offsets = [y.seconds - x.seconds for x, y in found]
    note = f"median offset {np.median(offsets):.3g} s" if all(x.absolute and y.absolute for x, y in found) else ""
    return Pair("inconsistent" if inverted else "consistent", fa, fb, len(ea), len(eb), 0.0,
                len(found) * 2 >= min(len(ea), len(eb)), f"events matched by their words ({len(found)}); order "
                "compared", note)


def _cmp_sequences(fa: Fact, fb: Fact) -> Pair | None:
    sa, sb = fa.value, fb.value
    match = _match_steps(sa.words, sb.words)
    found = [(i, j) for i, j in enumerate(match) if j is not None]
    if len(found) < 2:
        return None
    ok = all(j2 > j1 for (_, j1), (_, j2) in zip(found, found[1:]))
    missing = len(sa.items) - len(found)
    verdict = "consistent" if ok and not missing else "inconsistent" if not ok else "not_comparable"
    return Pair(verdict, fa, fb, len(sa.items), len(sb.items), 0.0, len(found) * 2 >= len(sa.items),
                f"items matched by their words ({len(found)} of {len(sa.items)}), order compared",
                f"{missing} item(s) not found" if missing else "")


def _cmp_pairs(fa: Fact, fb: Fact) -> Pair | None:
    """Name pairs a trace states against the pairs the code states (its call edges): every stated pair must be one
    the code makes by name; a name the code does not define is called out."""
    stmt, meas = (fa, fb) if isinstance(fa, Statement) else (fb, fa)
    if not isinstance(stmt, Statement) or isinstance(meas, Statement):
        return None
    stated = list(stmt.value)
    names_s = {n for p in stated for n in p}
    known = set(meas.get("defined") or ()) | set(meas.get("unresolved") or ()) | {n for p in meas.value for n in p}
    shared = names_s & known
    if len(shared) < 2 or len(shared) * 2 < len(names_s):
        return None
    edges = set(meas.value)
    fwd = sum(1 for p in stated if p in edges)
    rev = sum(1 for a, b in stated if (b, a) in edges)
    if max(fwd, rev) == 0:
        return None                            # not one stated pair is a pair of the code: they are not related
    if rev > fwd:
        stated = [(b, a) for a, b in stated]
    bad = [p for p in stated if p not in edges]
    defined = set(meas.get("defined") or ())
    note = ""
    if bad:
        x, y = bad[0]
        why = (f"the code defines nothing named '{clean(y, 40)}'" if y not in defined and
               y not in set(meas.get("unresolved") or ()) else
               f"'{clean(x, 40)}' never calls '{clean(y, 40)}' in the code")
        note = f"{len(bad)} of {len(stated)} stated pair(s) are not in the code; first: {why}"
    return Pair("inconsistent" if bad else "consistent", fa, fb, len(stated) if stmt is fa else len(edges),
                len(edges) if stmt is fa else len(stated), 0.0, True,
                f"each stated pair looked up among the code's {meas.get('edge')} edges, by name"
                + (" (the stated pairs read in reverse)" if rev > fwd else ""), note)


PER_ITEM_WORDS = frozenset("each every per apiece average typical".split())
_KIND_PAIRS = {("number", "number"), ("count", "count"), ("extent", "extent"), ("box", "box"), ("range", "range"),
               ("series", "series"), ("sequence", "events"), ("events", "sequence"), ("events", "events"),
               ("sequence", "sequence"), ("pairs", "pairs")}


def _candidates(A: Side, B: Side, q_aggs) -> list:
    out = []
    single = sum(f.kind == "series" for f in A.facts) == 1 and sum(f.kind == "series" for f in B.facts) == 1
    explicit = {f.ref for f in A.facts + B.facts if f.kind == "number" and f.aggregate}
    for fa in A.facts:
        for fb in B.facts:
            if len(q_aggs) == 1 and {fa.kind, fb.kind} == {"count", "number"}:
                # lane FX3 (class X-I): "Swimmers completed 943 laps" beside a column named Laps, when the question
                # names the aggregate (the season's total): the stated count is that column's aggregate
                cnt, num = (fa, fb) if fa.kind == "count" else (fb, fa)
                per_item = set(rr.tokens(cnt.sentence)) & PER_ITEM_WORDS     # lane FX4 (R4): "each swimmer
                #                                                                     completed 40 laps" is per item
                if isinstance(cnt, Statement) and isinstance(num, Quantity) and num.aggregate == q_aggs[0] \
                        and set(cnt.nouns) & set(num.words) and not cnt.get("bound") and not cnt.get("approx") \
                        and not per_item:
                    as_number = replace(cnt, kind="number", value=float(cnt.value), aggregate=None)
                    p = _cmp_number(as_number, num, q_aggs, explicit) if cnt is fa else \
                        _cmp_number(num, as_number, q_aggs, explicit)
                    if p is not None:
                        p.note = "; ".join(x for x in (p.note, "a stated count of what the column holds") if x)
                        out.append(p)
                continue
            if (fa.kind, fb.kind) not in _KIND_PAIRS:
                continue
            if isinstance(fa, Quantity) and isinstance(fb, Quantity) and fa.kind not in (
                    "count", "series", "events", "sequence"):
                continue                       # two measurements of numbers are compared only as counts or series
            p = None
            k = fa.kind
            if k == "number":
                p = _cmp_number(fa, fb, q_aggs, explicit)
            elif k == "count":
                p = _cmp_count(fa, fb)
            elif k == "extent":
                p = _cmp_extent(fa, fb)
            elif k == "box":
                p = _cmp_box(fa, fb)
            elif k == "range":
                p = _cmp_range(fa, fb)
            elif k == "series":
                p = _cmp_series(fa, fb, single)
            elif k == "pairs":
                p = _cmp_pairs(fa, fb)
            elif (fa.kind, fb.kind) == ("sequence", "events"):
                p = _cmp_sequence_events(fa, fb)
            elif (fa.kind, fb.kind) == ("events", "sequence"):
                p = _cmp_sequence_events(fb, fa)
                if p is not None:
                    p = replace(p, a=fa, b=fb, a_value=p.b_value, b_value=p.a_value)
            elif k == "events":
                p = _cmp_events(fa, fb)
            elif k == "sequence":
                p = _cmp_sequences(fa, fb)
            if p is not None:
                if isinstance(fa, Quantity) and isinstance(fb, Quantity) and not (
                        fa.kind == "series" and fa.words and set(fa.words) == set(fb.words)):
                    p.strong = False           # two measurements: compared when the question names them (or, for
                    p.note = (p.note + "; " if p.note else "") + "two measurements"   # series, one name)
                out.append(p)
                if len(out) >= MAX_PAIRS:
                    return out
    return _best_sequences(_resolve_numbers(out))


def _best_sequences(pairs: list) -> list:
    """Of a document's several readings as ordered steps (its list, the lines or sentences of a paragraph), only the
    one that matches the most events of a log is compared with it."""
    groups: dict[int, list] = {}
    for p in pairs:
        if {p.a.kind, p.b.kind} == {"sequence", "events"}:
            ev = p.a if p.a.kind == "events" else p.b
            groups.setdefault(id(ev), []).append(p)
    for group in groups.values():
        def found(p):
            return p.b_value if p.a.kind == "sequence" else p.a_value
        best = max(group, key=lambda p: (found(p), -abs((p.a_value if p.a.kind == "sequence" else p.b_value)
                                                         - found(p))))
        for p in group:
            if p is not best:
                p.strong = False
    return pairs


def _resolve_numbers(pairs: list) -> list:
    """A number statement that several quantities of one artifact could be (two columns with the same aggregate):
    only a uniquely best-connected one stays strong; a tie makes all of them weak (the subject is ambiguous). With
    one candidate, a stated aggregate is enough (the only column of numbers)."""
    groups: dict[tuple, list] = {}
    for p in pairs:
        if p.a.kind == "number":
            stmt = p.a if isinstance(p.a, Statement) else p.b
            groups.setdefault((id(stmt), p.a is stmt), []).append(p)
    for group in groups.values():
        if len(group) < 2:
            continue
        best = max(p.conn for p in group)
        top = [p for p in group if p.conn == best]
        for p in group:
            if p not in top or len(top) > 1 or best == 0:
                p.strong = False
                if p in top and len(top) > 1:
                    p.note = (p.note + "; " if p.note else "") + "more than one quantity fits this statement"
    return pairs


def _question_pairs(A: Side, B: Side, q_words: set) -> list:
    """Counts the question itself puts side by side ("one tone for each row"): a count each artifact has whose noun
    the question names, or the named one against the other artifact's primary count."""
    def named(side):
        return [f for f in side.facts if f.kind == "count" and isinstance(f, Quantity) and set(f.nouns) & q_words]
    na, nb = named(A), named(B)
    if not na and not nb:
        return []
    pa = na or [f for f in A.facts if f.kind == "count" and f.primary and isinstance(f, Quantity)]
    pb = nb or [f for f in B.facts if f.kind == "count" and f.primary and isinstance(f, Quantity)]
    if len(pa) != 1 or len(pb) != 1:
        return []
    fa, fb = pa[0], pb[0]
    if set(fa.nouns) & set(fb.nouns):
        return []                              # already a candidate by its nouns
    how = "the counts the question puts side by side" if na and nb else \
        "the count the question names, against the other artifact's main count"
    return [_count_pair(fa, fb, how, "paired by the question's words")]


# Words that name an artifact or its kind, not a quantity of it (lane FX, item 3): "Is the caption right about the
# image?" names two artifacts and no comparison, so these words never focus a comparison.
ARTIFACT_WORDS = frozenset("image images picture pictures photo photos photograph photographs caption captions figure "
                           "figures file files document documents report reports text texts note notes label labels "
                           "sound sounds recording recordings audio clip clips table tables spreadsheet spreadsheets "
                           "csv mesh meshes model models log logs code readme screenshot screenshots transcript "
                           "transcripts description descriptions title titles page pages artifact artifacts "
                           "drawing drawings svg png wav".split())


def _focus(p: Pair, q_words: set, q_aggs) -> int:
    words = set(p.a.words) | set(p.b.words) | set(p.a.nouns) | set(p.b.nouns)
    score = len(words & (q_words - ARTIFACT_WORDS))
    for f in (p.a, p.b):
        if f.aggregate and f.aggregate in q_aggs:
            score += 2
    return score


# What a question must name for a comparison of each kind to answer it (lane FX, item 3, ADR-080): a comparison
# decides only when the question names its aggregate, its count, its subject's words, or its kind by one of these.
KIND_CUES = {
    # "reading(s)": a measured value, the plain word for what a series holds (lane FX4: general vocabulary)
    "series": frozenset("series value values number numbers point points data plotted plot line lines bar bars chart "
                        "charts graph graphs drawn reading readings".split()),
    "pairs": frozenset("trace traces call calls called calling".split()),
    "sequence": frozenset("order ordered sequence event events step steps timeline before after chronology first last "
                          "then earlier later happened when".split()),
    "events": frozenset("order ordered sequence event events step steps timeline before after chronology first last "
                        "then earlier later happened when".split()),
    "box": frozenset("box bounding size sizes dimension dimensions extent extents width height depth wide tall deep "
                     "large big range ranges".split()),
    "extent": frozenset("box bounding size sizes dimension dimensions extent extents width height depth wide tall deep "
                        "large big range ranges pixel pixels resolution".split()),
    "range": frozenset("range ranges minimum maximum min max lowest highest between".split()),
    "count": rr.COUNT_CUES,
    "number": frozenset("duration long second seconds rate length area volume total sum mean average amount amounts "
                        "minimum maximum".split()),
}
# Words that ask what an image depicts or what a sound says: measured sizes, durations and statistics never answer
# them (an image or a sound contributes measurements only).
DEPICTION_CUES = frozenset("depict depicts depicted depicting describe describes described describing description "
                           "subject subjects scene scenes portray portrays portrayed show shows shown showing content "
                           "contents spoken speech lyrics sing sings sung utter utters uttered".split())
MEASURED_ONLY = ("raster", "frames", "signal")


def _named_kind(p, q_tokens: set) -> bool:
    return bool((KIND_CUES.get(p.a.kind, frozenset()) | KIND_CUES.get(p.b.kind, frozenset())) & q_tokens)


# ---- lane FX4 (R1): what a question must name before a comparison decides it -------------------------------------
# WHERE: words that name an artifact or the structure a quantity is read from (an archive's members, a drawing, a
# sound, a list, a sheet). QUANTITY_WORDS: general English words for quantities a comparison might measure, grouped by
# quantity; they are neither a case's words nor sufficient: a comparison decides only when the question names one of
# ITS quantities AND a WHERE word, and a quantity the question names that the comparison does not measure makes the
# answer "cannot" (`_foreign`).
WHERE_WORDS = ARTIFACT_WORDS | frozenset(
    "archive archives zip zips bundle bundles package packages member members folder folders directory diagram "
    "diagrams chart charts graph graphs plot plots sheet sheets list lists listing listings manifest manifests module "
    "modules source sources script scripts program programs timeline timelines route routes track "
    "tracks gpx cue cues workbook catalogue catalog inventory memo memos".split())
QUANTITY_WORDS = {
    "size": frozenset("size sizes byte bytes".split()),
    "digest": frozenset("checksum checksums hash hashes hashed digest digests sha256 sha1 sha512 md5".split()),
    "link": frozenset("link links linked linking connection connections connected connect connects edge edges join "
                      "joins joined relation relations relationship relationships".split()),
    "time": frozenset("time times timing timings onset onsets start starts started begin begins moment moments "
                      "timestamp timestamps".split()),
    "pitch": frozenset("pitch pitches frequency frequencies hz tuned".split()),
    "date": frozenset("date dates dated modified created".split()),
    "distance": frozenset("distance distances length lengths km kilometre kilometres kilometer kilometers mile "
                          "miles".split()),
    "direction": frozenset("direction directions whom directed arrow arrows reverse reversed".split()),
    "content": frozenset("content contents".split()),
    "colour": frozenset("colour colours color colors coloured colored".split()),
    "weight": frozenset("weight weights heavy heavier".split()),
}
_QUANTITY_OF = {w: q for q, words in QUANTITY_WORDS.items() for w in words}
_DIM_QUANTITY = {"duration": "time", "length": "distance", "data": "size", "mass": "weight", "pixels": "size"}
# what each kind of comparison measures (structure comparisons carry their own, `Pair.measures`)
_KIND_MEASURES = {"box": {"distance", "size"}, "extent": {"distance", "size"}, "events": {"time", "date"},
                  "sequence": {"time", "date"}, "times": {"time"}}
# words of judgement that name no quantity ("is it right", "as the caption says")
JUDGEMENT_WORDS = frozenset("right correct correctly wrong true false agree agrees match matches matching same say "
                            "says said state states stated give gives given claim claims really exactly accurate "
                            "accurately consistent".split())
SIZE_WORDS = frozenset("size sizes width height wide tall high dimension dimensions pixel pixels resolution frame "
                       "frames big".split()) - frozenset(("big",))


def _measures(p) -> set:
    if p.measures:
        return set(p.measures)
    out = set()
    for f in (p.a, p.b):
        out |= _KIND_MEASURES.get(f.kind, set())
        if f.kind == "number" and f.unit is not None and f.unit.dim in _DIM_QUANTITY:
            out.add(_DIM_QUANTITY[f.unit.dim])
    return out


def _foreign(p, q_tokens: set) -> set:
    """The quantities the question names that this comparison does not measure."""
    own = _measures(p)
    return {_QUANTITY_OF[w] for w in q_tokens if w in _QUANTITY_OF} - own


def _names_measure_and_where(p, q_tokens: set) -> bool:
    """R1: the question names one of the comparison's quantities AND where it is read from."""
    if p.measures:
        cues = set().union(*(QUANTITY_WORDS[q] for q in p.measures))
    else:
        cues = (KIND_CUES.get(p.a.kind, frozenset()) | KIND_CUES.get(p.b.kind, frozenset())) - WHERE_WORDS
    return bool(cues & q_tokens) and bool(WHERE_WORDS & q_tokens)


def _only_a_size_asked(q_words: set, q_tokens: set) -> bool:
    """R4: the question's content words are all artifact, judgement or size words, and one is a size word."""
    rest = set(q_words) - ARTIFACT_WORDS - JUDGEMENT_WORDS - SIZE_WORDS - DEPICTION_CUES
    return not rest and bool(SIZE_WORDS & q_tokens)


def _hidden_text(S) -> bool:
    """R2: a document that holds text its reader does not show (an HTML or Markdown comment, an RTF hidden group, an
    HTML element hidden by its style)."""
    if S.kind != "document":
        return False
    raw = S.raw or b""
    # the format is not known when a side is built from a bare representation (the production's sources): then a
    # text file's bytes are looked at as they are (lane FX4)
    textual = S.fmt in ("markdown", "html", "xhtml", "text", "md") or (not S.fmt and b"\x00" not in raw[:4096])
    if textual and b"<!--" in raw:
        return True
    if (S.fmt == "rtf" or (not S.fmt and raw[:5] == b"{\\rtf")) and re.search(rb"\\v(?![a-z])", raw):
        return True
    return any("hidden element" in str(n) for n in S.source_notes)


@dataclass
class Relation:
    answer: bool | None
    pairs: list
    decided_by: list                # indices into pairs
    reason: str
    shared: list = field(default_factory=list)
    describes: list = field(default_factory=list)
    timing: dict | None = None
    notes: list = field(default_factory=list)
    names: tuple = ("first", "second")
    specific: bool = False          # lane FX4 (R3): decided under a question naming what and where; only such an
    #                                 answer may become a durable link (`relate_cli.record_links`); lane FX5 (C4):
    #                                 computed in ONE place, `_specific`
    question_digest: str = ""       # lane FX5 (C4): which question (a hash, never its words), to replace a stale link

    def because(self, limit: int = 12) -> list[str]:
        out = [self.reason]
        for i in self.decided_by[:limit]:
            out.append(describe_pair(self.pairs[i], self.names))
        rest = [p for k, p in enumerate(self.pairs) if k not in self.decided_by and p.verdict != "not_comparable"
                and p.strong]
        for p in rest[:max(0, limit - len(self.decided_by))]:
            out.append("also " + describe_pair(p, self.names))
        for note in self.notes[:4]:
            out.append(note)
        return out

    def basis(self) -> str:
        """Machine-readable basis of the overall answer, for a link's `how` (no artifact text in it). Lane FX5 (C6c):
        the structure comparisons that took part come first, so that a rollback naming one of them finds every link
        it helped decide whatever else decided it (only the first four deciding comparisons are kept)."""
        order = sorted(self.decided_by, key=lambda i: self.pairs[i].a.kind not in STRUCTURE_KINDS)
        parts = [self.pairs[i].basis() for i in order[:4]]
        text = ";".join(parts) or "none"
        return re.sub(r"[^a-z0-9_.:=,;/+-]", "-", text.lower())[:200]

    def to_dict(self) -> dict:
        return {"answer": self.answer, "reason": self.reason,
                "pairs": [pair_dict(p, k in self.decided_by) for k, p in enumerate(self.pairs[:200])],
                "shared_names": self.shared[:50], "describes": self.describes, "timing": self.timing,
                "notes": self.notes}


def pair_dict(p: Pair, decided: bool) -> dict:
    def val(v):
        if isinstance(v, float):
            return round(v, 9)
        if isinstance(v, tuple):
            return [val(x) for x in v]
        if isinstance(v, dict):
            return {k: val(x) for k, x in v.items()}
        return v
    return {"verdict": p.verdict, "first": p.a.ref, "second": p.b.ref, "first_value": val(p.a_value),
            "second_value": val(p.b_value), "tolerance": p.tolerance, "unit": p.unit, "strong": p.strong,
            "decided": decided, "how": p.how, "note": p.note,
            "first_how": p.a.how, "second_how": p.b.how}


def describe_pair(p: Pair, names=("first", "second")) -> str:
    def what(f: Fact, who: str) -> str:
        if isinstance(f, Statement) and f.sentence:
            return f"{who} {f.how} (\"{clean(f.sentence, 120)}\")"
        return f"{who}: {f.how}"
    verdict = {"consistent": "agree", "inconsistent": "DISAGREE", "not_comparable": "cannot be compared"}[p.verdict]
    unit = f" {p.unit}" if p.unit else ""
    values = f"{_fmt(p.a_value)}{unit} against {_fmt(p.b_value)}{unit}" if not isinstance(p.a_value, dict) else \
        f"{p.a_value} against {p.b_value}{unit}"
    text = f"{verdict}: {values}; {p.how}. {what(p.a, names[0])}; {what(p.b, names[1])}."
    if p.note:
        text += f" {p.note}."
    return text


def compare(a, b, question: str | None = None) -> Relation:
    """Relate two artifacts (Sides, Decoded or representations). The answer is True when every pair that decides
    agrees, False when one of them disagrees, None when nothing comparable decides it."""
    A = a if isinstance(a, Side) else side(a)
    B = b if isinstance(b, Side) else side(b)
    q_words = set(rr.content_words(question or ""))
    q_tokens = set(rr.tokens(question or "")) | q_words
    q_aggs = rr.aggregate_named(question or "")
    pairs = _candidates(A, B, q_aggs)
    if set(rr.tokens(question or "")) & rr.COUNT_CUES:
        pairs += _question_pairs(A, B, q_words)
    pairs += _structure_pairs(A, B, question)
    for p in pairs:
        p.focus = _focus(p, q_words, q_aggs) if question else 0
        if isinstance(p.a, Quantity) and isinstance(p.b, Quantity) and p.verdict != "not_comparable" and \
                q_words & (set(p.a.nouns) | set(p.a.words)) and q_words & (set(p.b.nouns) | set(p.b.words)):
            p.strong = True                    # the question names what each of two measurements counts or shows
        if p.name_only and q_words & set(p.name_only) and set(rr.tokens(question or "")) & rr.COUNT_CUES:
            p.strong = True                    # the question, too, says the rows are what the name says (corroborated)
            p.note = "; ".join(x for x in (p.note, "the question counts the same thing") if x)
    names = (clean(A.name or "the first artifact", 60), clean(B.name or "the second artifact", 60))
    rel = Relation(None, pairs, [], "", shared_names(A, B), mentions(A, B), timing(A, B), A.notes + B.notes, names)
    rel.question_digest = question_digest(question)
    strong = [k for k, p in enumerate(pairs) if p.strong]
    named_counts = {n for f in A.facts + B.facts if f.kind == "count" and f.primary for n in f.nouns} & q_words \
        if set(rr.tokens(question or "")) & rr.COUNT_CUES else set()
    structure = [k for k in strong if pairs[k].a.kind in STRUCTURE_KINDS]
    asked = _counts_asked(question or "")
    hidden = [S for S in (A, B) if _hidden_text(S)]
    if hidden:                         # with or without a question (lane FX4: the production's question-less path)
        # lane FX4 (R2): only what a reader sees is evidence; a comment or a hidden group is not, and whether it
        # agrees with what is shown is not known
        rel.reason = ("cannot tell: one of them holds text a reader does not see (a comment or a hidden group); "
                      "hidden text is not evidence, and it is not known whether it agrees with what is shown")
        return rel
    asked_structure = _structure_question(A, B, question)
    if asked_structure:
        # lane FX5 (C1-C3): a question that one of the structure comparisons' closed grammars reads completely is
        # decided by that comparison alone, or is "cannot"; no other comparison answers it
        return _decide_by_structure(rel, pairs, asked_structure, question, A, B)
    decide = None
    if q_aggs:
        # the question asks about an aggregate: only a comparison of that aggregate may answer it
        decide = [k for k in strong if pairs[k].a.aggregate in q_aggs or pairs[k].b.aggregate in q_aggs]
        scope = "what the question asks about (" + ", ".join(q_aggs) + ")"
    elif asked:
        # lane FX3 (class X-B): "how many squares", "the number of stops": only a comparison of counts of that
        # thing answers it; a size or another count that agrees never stands in for it
        decide = [k for k in strong if pairs[k].a.kind == "count" and asked & _counted(pairs[k])]
        scope = "the count the question asks for (" + ", ".join(sorted(asked)) + ")"
        if not decide:
            rel.reason = ("cannot tell: the question asks for a count (" + ", ".join(sorted(asked)) + "); nothing "
                          "measured in one artifact counts that to compare with the other")
            return rel
    elif named_counts:
        decide = [k for k in strong if pairs[k].a.kind == "count" and
                  (set(pairs[k].a.nouns) | set(pairs[k].b.nouns)) & named_counts]
        scope = "the counts the question names"
        if not decide:
            decide = None       # lane FX3 (class X-G): "citation counts" names data, not a count to compare
    general = decide is None           # nothing in particular asked: the document as a whole against the other
    if decide is None:
        legacy = [k for k in strong if k not in structure]
        top = max((pairs[k].focus for k in legacy), default=0)
        decide = [k for k in legacy if pairs[k].focus == top] if top > 0 else legacy
        scope = "what the question names" if top > 0 else "everything the two artifacts let me compare"
        if question and top == 0:
            # lane FX4 (R1): a comparison decides only when the question names BOTH what it measures (a quantity
            # word of its kind) and where it is read from (a word naming an artifact or its structure); one cue
            # word is never enough
            decide = [k for k in legacy if _names_measure_and_where(pairs[k], q_tokens)]
            scope = "the comparison the question names, with where it is read from"
        # lane FX4 (team lead's ruling): with no question, only the comparisons that existed before Wave O-3
        # decide; members, links and times never do (they decide only a question their grammar reads, C1)
    if question and decide:
        # lane FX4 (R1): a quantity the question names that the deciding comparison does not measure (a pitch,
        # a date, a distance, a direction, a member's content...) makes it "cannot", whatever else agrees
        unmeasured = sorted(set().union(*(_foreign(pairs[k], q_tokens) for k in decide)))
        if unmeasured:
            rel.decided_by = []
            rel.reason = ("cannot tell: the question also asks about " + ", ".join(unmeasured) + ", which the "
                          "comparison that could answer it does not measure")
            return rel
    measured_only = [s for s in (A, B) if s.kind in MEASURED_ONLY]
    if question and measured_only and q_tokens & DEPICTION_CUES and not _only_a_size_asked(q_words, q_tokens):
        # lane FX4 (R4): a depiction question stays "cannot" whatever size adjective it carries; only a question
        # whose every content word names an artifact or a size ("does the caption describe the image's size")
        # is a question about a measurement
        rel.decided_by = []
        rel.reason = ("cannot tell: the question asks what " + ("an image depicts" if measured_only[0].kind != "signal"
                                                               else "a sound says") +
                      "; here an image or a sound gives only its measurements (sizes, durations, measured "
                      "statistics), never what it shows or says")
        return rel
    if not question and decide:
        # lane FX5 (C6b): with no question, a word inside a sentence never selects an aggregate ("last year's" is not
        # the column's last value, "total" not its sum), and a stated number is compared only with a measured number
        # that its own sentence names by the same words (a duration, a rate); anything else decides nothing
        untied = [k for k in decide if not _tied_without_question(pairs[k])]
        if untied:
            decide = [k for k in decide if k not in untied]
            if not decide:
                rel.reason = ("cannot tell: with no question, a stated number is compared only with a measured "
                              "number its own sentence names, never with a total, mean or extreme a word in it "
                              "suggests")
                return rel
    scoped = _out_of_scope(A, B, [pairs[k] for k in decide])
    if scoped:
        # lane FX5 (C4, the review's M4): a stated number whose own sentence narrows or denies it, whose document
        # excludes something from it, or that the document states twice with two values is not compared
        rel.decided_by = []
        rel.reason = "cannot tell: " + scoped
        return rel
    verdicts = [pairs[k].verdict for k in decide]
    rel.decided_by = decide
    if not decide and question and pairs and strong:
        rel.reason = ("cannot tell: the question does not name anything both artifacts let me measure (what could "
                      "be compared is not what it asks)")
        return rel
    if not decide:
        rel.reason = ("cannot tell: nothing measured in one of them is stated or shown in the other in a way that "
                      "can be compared" if not pairs else
                      "cannot tell: the only comparable facts are not tied to one subject clearly enough")
    elif "inconsistent" in verdicts:
        rel.answer = False
        rel.reason = f"they disagree ({verdicts.count('inconsistent')} of {len(decide)} comparison(s) of {scope})"
    elif all(v == "consistent" for v in verdicts) and scope.startswith("everything") and _dangling(A, B, pairs):
        st = _dangling(A, B, pairs)[0]
        rel.reason = ("cannot tell: what could be compared agrees, but a statement about the same subject could not "
                      f"be compared ({st.how}: it does not say which total, mean or extreme it is)")
    elif all(v == "consistent" for v in verdicts) and general and _unread_about_table(A, B, pairs):
        st = _unread_about_table(A, B, pairs)[0]
        rel.reason = ("cannot tell: what could be compared agrees, but the document states a number about the table "
                      f"that could not be compared ({st.how}: it does not say which total, mean or extreme it is)")
    elif all(v == "consistent" for v in verdicts):
        rel.answer = True
        rel.reason = f"they agree ({len(decide)} comparison(s) of {scope}, each within its stated tolerance)"
    else:
        rel.reason = f"cannot tell: {scope} cannot be compared fully"
    rel.specific = _specific(rel, question, A, B, None if scope.startswith("everything") else decide)
    _mark_unseen(rel, A, B)
    return rel


# ---- lane FX5 (ADR-081, C1-C4): which comparison may decide, and which answer may become a durable link -----------

def question_digest(question: str | None) -> str:
    """16 hex digits of SHA-256 of the question, case and spacing folded ("" without a question): a recorded link
    names the question it answers without keeping its words."""
    if not question:
        return ""
    import hashlib
    return hashlib.sha256(" ".join(question.casefold().split()).encode()).hexdigest()[:16]


_STRUCTURE_WHAT = {"size": "the sizes of an archive's members", "digest": "the digests of an archive's members",
                   "link": "the links a drawing draws", "time": "the times a sound's tones start"}


def _structure_question(A: Side, B: Side, question: str | None) -> tuple:
    """The structure grammars (`relate_grammar.GRAMMARS`) that read the question completely, for two artifacts of
    the kinds that comparison relates (an archive and a listing, a drawing and a text, a sound and a text)."""
    if not question:
        return ()
    from . import relate_grammar as rg
    kinds = {A.kind, B.kind}
    grams = []
    if "bundle" in kinds and kinds & {"document", "tree"}:
        grams += ["size", "digest"]
    if "drawing" in kinds and "document" in kinds:
        grams.append("link")
    if "signal" in kinds and "document" in kinds:
        grams.append("time")
    names = (A.name, B.name)
    return tuple(g for g in grams if rg.parse(question, rg.GRAMMARS[g], names).complete)


def _decide_by_structure(rel: Relation, pairs: list, asked: tuple, question: str, A: Side, B: Side) -> Relation:
    """C1-C3: exactly one structure comparison that read the question completely, and read and matched every line
    and every element, answers; anything else is "cannot" with the reason."""
    mine = [k for k, p in enumerate(pairs) if p.a.kind in STRUCTURE_KINDS]
    able = [k for k in mine if pairs[k].read is not None and pairs[k].read.complete]
    asks = " and ".join(_STRUCTURE_WHAT[g] for g in asked)
    if len(able) != 1:
        notes = [pairs[k].note for k in mine if pairs[k].note.startswith("cannot: ")]
        if len(able) > 1:
            why = "more than one comparison could answer it"
        elif notes:
            why = notes[0][len("cannot: "):]
        elif mine:
            why = "the comparison these two artifacts allow does not measure what the question asks"
        else:
            why = ("these are not an archive and a plain listing of its members, a node-link drawing and a plain "
                   "list of its links, or a sound of steady tones and a plain cue sheet, read completely")
        rel.reason = f"cannot tell: the question asks about {asks}; {why}"
        return rel
    k = able[0]
    p = pairs[k]
    rel.decided_by = [k]
    if p.verdict == "consistent":
        rel.answer = True
        rel.reason = f"they agree ({asks}: every line and every element read and matched)"
    elif p.verdict == "inconsistent":
        rel.answer = False
        rel.reason = f"they disagree ({asks})"
    else:
        note = p.note[len("cannot: "):] if p.note.startswith("cannot: ") else p.note
        rel.reason = "cannot tell: " + (note or f"{asks} cannot be compared fully")
    rel.specific = _specific(rel, question, A, B, [k])
    _mark_unseen(rel, A, B)
    return rel


# Words with which a sentence narrows, denies, conditions or moves in time what it states (C4, the review's M4: "not
# counting the silence", "it is not true that", "before the refund", "the full photo is ..."). A stated number whose
# own sentence holds one is not compared: a closed list of a decline rule, never of an answer.
_SCOPE_WORDS = frozenset("""not no never none nor neither without cannot except excepting excluding exclude excludes
excluded apart besides aside other minus plus including include includes included only just alone counting net gross
subtotal before after until till since prior previous previously earlier later formerly once originally initially
then now currently yet if unless would could should might may expected expect expects planned plan plans budget
budgeted target targets forecast forecasts projected estimate estimated estimates each every per apiece first last
full""".split())
# Words with which a document excludes something from what it states, wherever they stand in it.
_EXCLUDING = frozenset("except excepting excluding exclude excludes excluded apart besides aside minus counting".split())
_SCOPED_KINDS = ("number", "count", "extent", "box", "range")


def _out_of_scope(A: Side, B: Side, deciding: list) -> str | None:
    """Why the older comparisons that would decide must not (C4), or None."""
    seen: dict[int, list] = {}
    for p in deciding:
        for f, other in ((p.a, p.b), (p.b, p.a)):
            if not isinstance(f, Statement) or f.kind not in _SCOPED_KINDS or not f.sentence:
                continue
            toks = set(rr.tokens(f.sentence))
            if toks & _SCOPE_WORDS or any(t.endswith("n't") for t in toks):
                return ("a number compared is stated in a sentence that narrows, denies or conditions it (\""
                        + clean(f.sentence, 80) + "\")")
            owner = A if any(x is f for x in A.facts) else B if any(x is f for x in B.facts) else None
            if owner is not None and owner.kind == "document" and set(rr.tokens(owner.words)) & _EXCLUDING:
                return "the document excludes something from what it states, and what is not read"
            value = p.a_value if f is p.a else p.b_value
            seen.setdefault(id(other), []).append((value, p.tolerance or 0.0))
    for values in seen.values():
        for (v1, t1), (v2, _) in itertools.combinations(values, 2):
            same = v1 == v2
            if not same and isinstance(v1, (int, float)) and isinstance(v2, (int, float)):
                same = abs(float(v1) - float(v2)) <= t1 + REL_EPS * max(1.0, abs(float(v1)))
            if not same:
                return "the document states the quantity compared more than once, with different values"
    return None


UNSEEN_NOTE = "not recorded: this document may hold text a reader does not see"
_TABLE_PLAIN = ("csv", "tsv")


def _shown_as_written(S: Side) -> bool:
    """A text file whose every character is shown as it is written: it decodes, and holds no control, format,
    private-use or separator character (a bidirectional override, a zero-width space, an escape sequence)."""
    import unicodedata
    if not S.raw:
        return False
    from .adapters.plain import decode_text
    try:
        text = decode_text(bytes(S.raw))[0]
    except Exception:
        return False
    return not any(unicodedata.category(ch) in ("Cc", "Cf", "Co", "Cs", "Zl", "Zp") and ch not in "\t\n\r"
                   for ch in text[:rr.MAX_TEXT])


def _visibility_unknown(S: Side) -> bool:
    """Lane FX5 (C6a): whether this side may hold text a reader does not see, as far as this code can establish.
    Established as visible only for: a plain text document or a CSV/TSV table shown as written (C2), a plain SVG
    drawing every element of which is evaluated (`_plain_svg`), and the kinds whose content is data, never rendered
    text (sound, image, video, mesh, archive, map features, code graph, data tree). A side built from a bare
    representation (no format: its reader's notes are not known) is never established. No reader of a styled format
    (Markdown, HTML, RTF, DOCX, ODT, PDF, spreadsheets) reports that it holds no hidden text, so none is."""
    if not S.fmt:
        return True
    if S.kind == "document":
        return not (S.fmt == "text" and _shown_as_written(S))
    if S.kind == "table":
        return not (S.fmt in _TABLE_PLAIN and _shown_as_written(S))
    if S.kind == "drawing":
        return S.fmt != "svg" or _plain_svg(S.raw or b"") is not None
    return S.kind not in ("signal", "raster", "frames", "mesh", "bundle", "features", "graph", "tree")


def _mark_unseen(rel: Relation, A: Side, B: Side) -> None:
    """An answer shown from a side that may hold unseen text says that it is not recorded (and `specific` is False)."""
    if rel.answer is not None and (_visibility_unknown(A) or _visibility_unknown(B)) and UNSEEN_NOTE not in rel.notes:
        rel.notes = [UNSEEN_NOTE] + list(rel.notes)


def _tied_without_question(p: Pair) -> bool:
    """Lane FX5 (C6b): whether a comparison may decide with no question. Number and range comparisons only when
    neither side is an aggregate (a word in a sentence never selects one) and the statement's own words name the
    measured quantity (beyond a unit); every other kind as before."""
    if p.a.kind not in ("number", "range"):
        return True
    stmt, meas = (p.a, p.b) if isinstance(p.a, Statement) else (p.b, p.a)
    if stmt.aggregate is not None or meas.aggregate is not None or p.note:
        return False
    return bool(set(stmt.words) & set(meas.words))


def _specific(rel: Relation, question: str | None, A: Side, B: Side, decide) -> bool:
    """The ONE gate for a durable consistent_with / contradicts link, on every path (lane FX5, C4). True only for a
    decided answer to a question that a closed grammar read completely: a structure comparison's own grammar (whose
    C2 and C3 conditions hold by construction once it decides), or, for the older comparisons, the general
    vocabulary of aggregates, counts and measures together with the words of the facts compared
    (`relate_grammar.legacy_parse`). An answer without a question, or decided by "everything", is never specific."""
    if rel.answer is None or not question or not decide:
        return False
    if _visibility_unknown(A) or _visibility_unknown(B):
        return False                        # lane FX5 (C6a): a side may hold text a reader does not see
    from . import relate_grammar as rg
    deciding = [rel.pairs[k] for k in decide]
    structural = [p.a.kind in STRUCTURE_KINDS for p in deciding]
    if all(structural):
        return all(p.read is not None and p.read.complete for p in deciding)
    if any(structural):
        return False
    words: set = set()
    for p in deciding:
        for f in (p.a, p.b):
            words |= set(f.words) | set(f.nouns)
    return rg.legacy_parse(question, (A.name, B.name), words).complete


_COUNT_ASKED = re.compile(r"\b(?:number|count|amount)\s+of\s+(?:the\s+|all\s+)?([a-z][a-z-]{2,})"
                          r"|\bhow\s+many\s+([a-z][a-z-]{2,})|\bas\s+many\s+([a-z][a-z-]{2,})", re.I)


def _counts_asked(question: str) -> set:
    """What a question asks to be counted ("the number of squares", "how many stops", "as many beeps"), singular."""
    out = set()
    for m in _COUNT_ASKED.finditer(question or ""):
        word = next(g for g in m.groups() if g)
        if word.lower() not in ("the", "them", "these", "those", "times"):
            out.add(rr.singular(word.lower()))
    return out


def _counted(p: Pair) -> set:
    """What a count comparison counts: its nouns only (lane FX4: the other words of a stated count's sentence, "7
    shapes, 5 of them squares", are not what that count counts)."""
    return set(p.a.nouns) | set(p.b.nouns)


def _unread_about_table(A: Side, B: Side, pairs: list) -> list:
    """Lane FX3 (class X-A): number statements of a document that no comparison used, beside a table whose numeric
    columns they could be about (a unit of the same dimension, or a unit on one side only): "Fuel bought: 778.54
    litres" when nothing says which aggregate it is. While one exists, the agreement of the other comparisons is not
    the document's agreement with the table."""
    used = {id(p.a) for p in pairs} | {id(p.b) for p in pairs}
    out = []
    for S, O in ((A, B), (B, A)):
        if S.kind != "document" or O.kind not in ("table", "tree"):
            continue
        columns = [q for q in O.facts if q.kind == "number" and q.aggregate and isinstance(q, Quantity)]
        for st in S.facts:
            if st.kind != "number" or not isinstance(st, Statement) or st.get("derived") or id(st) in used or \
                    _yearlike(st):
                continue
            if any(rr.compatible(st.unit, q.unit) for q in columns):
                out.append(st)
    return out


def _dangling(A: Side, B: Side, pairs: list) -> list:
    """Number statements tied to the other artifact (a unit of the same dimension, or a shared word, with one of
    its quantities) that no comparison used: while one exists, agreement of the rest is not agreement."""
    used = {id(p.a) for p in pairs} | {id(p.b) for p in pairs}
    out = []
    for S, O in ((A, B), (B, A)):
        others = [q for q in O.facts if q.kind == "number" and not q.get("derived")]
        for st in S.facts:
            if st.kind != "number" or not isinstance(st, Statement) or st.get("derived") or id(st) in used or \
                    _yearlike(st):
                continue
            for q in others:
                same_dim = st.unit is not None and q.unit is not None and st.unit.dim == q.unit.dim
                if same_dim or (rr.compatible(st.unit, q.unit) and set(st.words) & set(q.words)):
                    out.append(st)
                    break
    return out


# ---- structure across two sides (lane FX3, ADR-081; rebuilt by construction in lane FX5) ----------------------------
# Three measured comparisons that need both artifacts at once: an archive's members against a listing of them, a
# node-link drawing's links against lines naming two of its labels, a sound's tone onsets against lines of elapsed
# times. Lane FX5 (ADR-081, C1-C3) builds them so that a wrong answer cannot come from what the code does not read:
# - C1: a comparison decides only a question its closed grammar reads COMPLETELY (`relate_grammar`); any other word
#   leaves it "cannot";
# - C2: a listing, a link list or a cue sheet is read only from a PLAIN TEXT file, from its own bytes, every character
#   printable ASCII (no format, control or look-alike character): Markdown, HTML, RTF, DOCX, ODT, PDF, JSON and every
#   other source with a notion of hidden, styled or structured text are "cannot: this comparison reads plain listings
#   only";
# - C3: every line must be read and matched, and every element of the other artifact classified (each member a
#   regular file the decoder read, each mark of a drawing a labelled node, its label or an edge with fully evaluated
#   visible style, each stretch of a sound a steady tone or silence); anything else, a duplicate, a bound reached, or
#   a value that another reading (another unit, another digest algorithm, rounding against truncation) would explain
#   makes it "cannot".

STRUCTURE_KINDS = ("members", "links", "times")
_DIGESTS = {32: "md5", 40: "sha1", 64: "sha256", 128: "sha512"}
_LIST_MARK = re.compile(r"^\s*(?:[-*+]|\d{1,4}[.)])\s+")
_HEX_LINE = re.compile(r"^\s*([0-9a-fA-F]{32,128})\s+\*?(\S+)\s*$")
_SIZE_FIRST = re.compile(r"^\s*(\d{1,15})\s+(\S+)\s*$")
_SIZE_LAST = re.compile(r"^\s*(\S+)\s+(\d{1,15})\s*(b|bytes?)?\s*$", re.I)
_PATHLIKE = re.compile(r"^[^\s:]*(?:/[^\s/]+|\.[A-Za-z0-9]{1,8})$")
_TIME_LINE = re.compile(r"^\s*(?:(\d{1,2}):)?(\d{1,2}):(\d{2})(?:[.,](\d{1,6}))?(?:\s+([A-Za-z]+)(?:\s+\d{1,4})?)?\s*$")
MAX_LISTING_LINES = 20_000
MAX_DIGEST_BYTES = 256 << 20            # member bytes hashed per comparison, at most (as max_decoded_bytes)
MAX_DRAWING_SHAPES = 5_000              # marks of a node-link drawing read (lane FX4 review L5: a bound on the work)
TIMES_TOLERANCE_S = 0.05                # ten 5 ms analysis frames of `tone_segments`
PLAIN_ONLY = "cannot: this comparison reads plain listings only (a plain text file of printable characters)"
# The joins a link line may hold between two labels: general English for "is linked to" (direction is not read).
_LINK_JOIN = re.compile(
    r"^\s*(?:->|-->|<->|--|-|→|–|—|to|reports? to|depends? on|feeds?(?: into)?|leads? to|links? to|"
    r"is linked to|connects? to|is connected to|calls|is a child of|is the child of|is a parent of|is the parent of|"
    r"sends? to|hands? over to|passes to|points to|manages|is managed by|uses|imports)\s*$")
# A cue sheet line is a bare elapsed time, or a time and one word naming the sound event (the same on every line).
_EVENT_WORDS = frozenset("tone beep chime bell gong click pulse cue onset ding".split())
_LABEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9 ._'&-]{0,78}")


def _plain_lines(S: Side, extra: str = "") -> tuple[list[str], bool] | None:
    """C2: (the lines of a plain text file, whether all of them were read), from its own bytes; None for any other
    source, or when a character is not printable ASCII (or one of `extra`): a control, format or look-alike character
    may show something other than what is read."""
    if S.kind != "document" or S.fmt != "text" or not S.raw:
        return None
    from .adapters.plain import decode_text
    try:
        text = decode_text(bytes(S.raw))[0]
    except Exception:
        return None
    text = text.replace("\r\n", "\n")
    allowed = set(extra)
    for ch in text[:rr.MAX_TEXT]:
        if not (" " <= ch <= "~" or ch in "\t\n" or ch in allowed):
            return None
    lines = text[:rr.MAX_TEXT].split("\n")
    complete = len(text) <= rr.MAX_TEXT and len(lines) <= MAX_LISTING_LINES
    return [_LIST_MARK.sub("", ln) for ln in lines[:MAX_LISTING_LINES]], complete


def _all_members_read(B: Side) -> bool:
    """Every member of the archive was read by the decoder (none listed only, refused or cut off)."""
    if B.partial:
        return False
    for note in B.source_notes:
        m = re.match(r"(\d+) member\(s\): (\d+) decoded, (\d+) listed only, (\d+) refused, (\d+) failed", str(note))
        if m:
            return m.group(3) == "0" and m.group(4) == "0"
    return False


def _canon(path: str) -> str:
    import posixpath
    p = posixpath.normpath(path)
    return (p[2:] if p.startswith("./") else p).casefold()


def _member_table(B: Side) -> dict | None:
    """{member path: size measured from its bytes} of a ZIP or TAR archive every member of which was read, each a
    regular file with a printable ASCII name; None otherwise (C3: an element not classified)."""
    from .outcome import member_path
    if B.fmt not in ("zip", "tar") or not _all_members_read(B):
        return None
    out, canon = {}, set()
    for m in getattr(B.rep, "members", ()) or ():
        fmt = getattr(m.detected, "format", None)
        if fmt == "directory":
            continue
        if fmt == "python-package":
            continue                         # the decoder's combined graph of the Python members: not a member
        path = member_path(str(m.path))
        if not path or not all(" " < ch <= "~" for ch in path) or _canon(path) in canon:
            return None
        canon.add(_canon(path))
        out[path] = int(m.size)
    return out or None


def _listing_from_lines(lines: list[str]) -> tuple[list[tuple[str, str, object, bool]], int]:
    """((path, property, value, unit stated) per listed line, lines not read): "<hex digest>  <path>" (sha256sum and
    kin), "<size>  <path>" or "<path>  <size>[ bytes]". A non-empty line that is not one of these is not read."""
    out, unread = [], 0
    for ln in lines:
        if not ln.strip():
            continue
        m = _HEX_LINE.match(ln)
        if m and len(m.group(1)) in _DIGESTS and _PATHLIKE.match(m.group(2)):
            out.append((m.group(2), _DIGESTS[len(m.group(1))], m.group(1).lower(), True))
            continue
        m = _SIZE_FIRST.match(ln)
        if m and _PATHLIKE.match(m.group(2)) and not rr.is_number_like(m.group(2)):
            out.append((m.group(2), "size", int(m.group(1)), False))
            continue
        m = _SIZE_LAST.match(ln)
        if m and _PATHLIKE.match(m.group(1)) and not rr.is_number_like(m.group(1)):
            out.append((m.group(1), "size", int(m.group(2)), bool(m.group(3))))
            continue
        unread += 1
    return out, unread


def _digests(B: Side, wanted: dict) -> dict:
    """{path: hex digest} of the listed ZIP members, each read once by ONE safe reader built once (linear in the
    members and bounded by MAX_DIGEST_BYTES in all); a member that cannot be read is left out."""
    import hashlib
    if not B.raw or B.fmt != "zip":
        return {}
    try:
        from .outcome import member_path
        from .safe_b1 import ZipReader
        from .types import Limits
        limits = Limits()
        reader = ZipReader(B.raw, max_members=limits.max_members, partial_ok=False)
    except Exception:
        return {}
    entries = {member_path(e.name): e for e in reader.entries}
    left, out = MAX_DIGEST_BYTES, {}
    for path, algo in wanted.items():
        entry = entries.get(path)
        if entry is None or entry.size > left:
            continue
        try:
            body = reader.read(entry, limits=limits, out_limit=left)
        except Exception:
            continue
        left -= len(body)
        out[path] = hashlib.new(algo, body).hexdigest()
    return out


def _other_unit(listed: int, size: int) -> bool:
    """Whether a listed number would be this size in a common unit other than bytes (kB, KiB, 512- or 4096-byte
    blocks, MB, MiB; rounded, cut or raised): then a listing that states no unit does not disagree."""
    for div in (1000, 1024, 512, 4096, 1_000_000, 1 << 20):
        q = size / div
        if listed in (math.floor(q), math.ceil(q), round(q)):
            return True
    return listed == math.ceil(size / 4096) * 4           # 4 KiB blocks counted in KiB (du)


def _unreadable(kind: str, what: str, note: str, read=None) -> Pair:
    """A structure comparison that was recognised but cannot answer: a pair that never decides, with the reason."""
    meas = _q(kind, f"structure.{kind}", (), how=what)
    stmt = _s(kind, f"document.{kind}", (), how=what)
    pair = Pair("not_comparable", meas, stmt, 0, 0, None, True, what, note)
    pair.read = read
    return pair


def _members_pair(B: Side, D: Side, question: str | None) -> Pair | None:
    """B an archive, D a plain listing of its members with a size or a digest each."""
    from . import relate_grammar as rg
    if B.kind != "bundle" or B.fmt not in ("zip", "tar"):
        return None
    read = _plain_lines(D)
    if read is None:
        if D.kind in ("document", "tree") and B.fmt in ("zip", "tar"):
            return _unreadable("members", "a listing of the archive's members", PLAIN_ONLY)
        return None
    lines, whole = read
    listed, unread = _listing_from_lines(lines)
    if not listed:
        return None
    table = _member_table(B)
    if table is None:
        return _unreadable("members", "a listing of the archive's members", "cannot: not every member of the "
                           "archive was read as a regular file with a plain name")
    from .outcome import member_path
    paths = [member_path(p) for p, _, _, _ in listed]
    if None in paths or len({_canon(p) for p in paths}) != len(paths) or \
            not all(" " < ch <= "~" for p in paths for ch in p):
        return _unreadable("members", "a listing of the archive's members", "cannot: the listing names a member "
                           "twice, or by a name that is not plain")
    matched = [p for p in paths if p in table]
    if not matched or len(matched) * 2 < len(listed):
        return None                         # not a listing of this archive
    props = {prop for _, prop, _, _ in listed}
    prop = props.pop() if len(props) == 1 else None
    grammar = rg.GRAMMARS["size" if prop == "size" else "digest"]
    q_read = rg.parse(question or "", grammar, (B.name, D.name)) if question else None
    note: list[str] = []
    if prop is None:
        note.append("the listing mixes sizes and digests")
    if unread:
        note.append(f"{unread} line(s) of the listing could not be read as a member and its value")
    if not whole:
        note.append("the listing is longer than what is read of it: its end was not read")
    algo = prop if prop not in (None, "size") else None
    named = None
    if algo:
        named = (q_read.algorithm if q_read is not None else None) or rg.algorithm_in_name(D.name)
        if named and named != algo:
            note.append(f"the listing's digests have the length of {algo}, not of the {named} named")
    digests = _digests(B, {p: algo for p in matched}) if algo and not note else {}
    unit_stated = prop == "size" and all(u for _, _, _, u in listed)
    bytes_named = unit_stated or bool(q_read is not None and q_read.bytes_named)
    agree, disagree, explained, absent, ambiguous, unhashed = [], [], [], [], [], 0
    canon_members = {_canon(p): p for p in table}
    folders = {_canon(p).rsplit("/", k)[0] for p in table for k in range(1, _canon(p).count("/") + 1)}
    for path, (_, _, value, _) in zip(paths, listed):
        if path not in table:
            c = _canon(path)
            # a name that differs only in case or path form, a folder of members, or a name inside a member (a
            # nested archive): what the listing means by it is not plain
            plain_absent = c not in canon_members and c not in folders and \
                not any(c.startswith(m + "/") for m in canon_members)
            (absent if plain_absent else ambiguous).append(path)
            continue
        if prop == "size":
            got = table[path]
            if got == value:
                agree.append(path)
            elif not bytes_named and _other_unit(int(value), got):
                explained.append(path)
            else:
                disagree.append(path)
        elif algo:
            got = digests.get(path)
            if got is None:
                unhashed += 1
            else:
                (agree if got == value else disagree).append(path)
    unlisted = len(set(table) - set(paths))
    if absent:
        note.append(f"{len(absent)} listed member(s) are not in the archive")
    if ambiguous:
        note.append(f"{len(ambiguous)} listed name(s) differ from a member's only in case or path form, or name a "
                    "folder or a name inside a member")
    if unlisted:
        note.append(f"{unlisted} member(s) of the archive are not listed")
    if unhashed:
        note.append(f"{unhashed} member(s) could not be read again to compute a digest (only ZIP members are)")
    if explained:
        note.append(f"{len(explained)} listed size(s) differ in bytes but would agree in another unit, and the "
                    "listing and the question state no unit")
    if algo and disagree and not agree and not named:
        note.append("no digest agreed, and neither the question nor the listing's name says which algorithm made "
                    "it: its length alone does not say")
    blocked = prop is None or unread or not whole or (algo and named and named != algo) or ambiguous or unhashed \
        or explained or (algo and disagree and not agree and not named)
    if blocked:
        verdict = "not_comparable"
    elif disagree or (absent and unlisted):
        # a value that disagrees is a disagreement whichever way the question is read; a listed member the archive
        # lacks, or a member the listing leaves out, is one only if both happen ("does the list give the sizes of
        # all the files" holds with an extra line; "are the listed sizes the archive's" holds with a member left out)
        verdict = "inconsistent"
    elif absent or unlisted or not agree:
        verdict = "not_comparable"
    else:
        verdict = "consistent"
    what = "sizes in bytes" if prop == "size" else f"{(algo or 'mixed').upper()} digests"
    meas = _q("members", "bundle.members.listing", tuple(sorted(table)), how=f"each member's {what}, measured "
              f"from its bytes", nouns=("member", "file"))
    stmt = _s("members", "document.listing", tuple(paths), how=f"{len(listed)} line(s) listing a member with "
              f"its {what}", nouns=("member", "file"))
    pair = Pair(verdict, meas, stmt, len(agree), len(listed), 0.0, True,
                f"each listed member's {what} against the archive's member ({len(agree)} of {len(listed)} agree)",
                "; ".join(note))
    pair.measures = frozenset({"size"} if prop == "size" else {"digest"})
    pair.complete = verdict != "not_comparable"
    pair.read = q_read
    return pair


# ---- a node-link drawing whose every mark is read (C3) --------------------------------------------------------------

_SVG_NS = "{http://www.w3.org/2000/svg}"
_SVG_GEOMETRY = {"svg": {"width", "height", "viewBox", "version"}, "g": set(), "title": set(), "desc": set(),
                 "rect": {"x", "y", "width", "height", "rx", "ry"}, "circle": {"cx", "cy", "r"},
                 "ellipse": {"cx", "cy", "rx", "ry"}, "line": {"x1", "y1", "x2", "y2"}, "polyline": {"points"},
                 "polygon": {"points"}, "path": {"d"}, "text": {"x", "y"}}
_SVG_PAINT = {"id", "fill", "stroke", "stroke-width", "stroke-linecap", "stroke-linejoin", "font-family",
              "font-size", "font-weight", "text-anchor", "transform"}
_NAMED_COLOURS = {"black": (0, 0, 0), "white": (255, 255, 255), "gray": (128, 128, 128), "grey": (128, 128, 128),
                  "dimgray": (105, 105, 105), "dimgrey": (105, 105, 105), "darkgray": (169, 169, 169),
                  "darkgrey": (169, 169, 169), "silver": (192, 192, 192), "red": (255, 0, 0), "maroon": (128, 0, 0),
                  "green": (0, 128, 0), "blue": (0, 0, 255), "navy": (0, 0, 128), "purple": (128, 0, 128),
                  "teal": (0, 128, 128), "olive": (128, 128, 0)}
_PLAIN_NUMBER = re.compile(r"\s*([-+]?(?:\d+\.?\d*|\.\d+))(?:px)?\s*")


def _colour(value: str | None):
    """(r, g, b), "none", or None when the value is not one this code evaluates (alpha, functions, keywords...)."""
    v = (value or "").strip().lower()
    if v == "none":
        return "none"
    if v in _NAMED_COLOURS:
        return _NAMED_COLOURS[v]
    m = re.fullmatch(r"#([0-9a-f]{3}|[0-9a-f]{6})", v)
    if not m:
        return None
    h = m.group(1)
    h = "".join(c * 2 for c in h) if len(h) == 3 else h
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _visible_on_white(rgb) -> bool:
    """A colour with a contrast of at least 1.5 against white (WCAG relative luminance)."""
    def lin(c):
        c = c / 255.0
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    lum = 0.2126 * lin(rgb[0]) + 0.7152 * lin(rgb[1]) + 0.0722 * lin(rgb[2])
    return 1.05 / (lum + 0.05) >= 1.5


def _plain_svg(raw: bytes) -> str | None:
    """None when every element and attribute of an SVG is one this code fully evaluates and every mark is visible:
    elements svg, g, title, desc, rect, circle, ellipse, line, polyline, polygon, path, text (no child); geometry
    attributes, plain colours (#rgb, #rrggbb, a few names), widths and font sizes in pixels, translations only; no
    style, class, opacity, display, visibility, marker, dash, filter, mask, clip, use, defs or anything else. Else a
    short reason (C3: a drawing whose style is not fully evaluated is not read)."""
    from .safe_b2 import parse_xml
    from .types import Limits
    try:
        root = parse_xml(raw, Limits(), "the SVG document")
    except Exception:
        return "the drawing could not be parsed again"
    if root.tag not in (_SVG_NS + "svg", "svg"):
        return "the drawing's root is not svg"
    width, height = (_PLAIN_NUMBER.fullmatch(root.get(k) or "") for k in ("width", "height"))
    if not width or not height:
        return "the drawing states no plain width and height"
    scale = 1.0
    if root.get("viewBox") is not None:
        vb = [float(x) for x in re.findall(r"[-+]?(?:\d+\.?\d*|\.\d+)", root.get("viewBox"))]
        if len(vb) != 4 or vb[0] != 0 or vb[1] != 0 or vb[2] <= 0 or vb[3] <= 0:
            return "the drawing's viewBox is not 0 0 width height"
        scale = min(float(width.group(1)) / vb[2], float(height.group(1)) / vb[3])
    if not scale > 0:
        return "the drawing has no size"

    def walk(el, inherited, depth):
        tag = el.tag[len(_SVG_NS):] if el.tag.startswith(_SVG_NS) else el.tag
        if tag not in _SVG_GEOMETRY or (tag == "svg" and depth) or depth > 32:
            return f"an element ({clean(str(tag), 20)}) this comparison does not evaluate"
        for name, value in el.attrib.items():
            if name not in _SVG_GEOMETRY[tag] and name not in _SVG_PAINT:
                return f"an attribute ({clean(str(name), 30)}) this comparison does not evaluate"
            if name == "transform" and not re.fullmatch(r"\s*(?:translate\(\s*[-+.\d]+(?:[\s,]+[-+.\d]+)?\s*\)\s*)+",
                                                         value):
                return "a transform other than a translation"
        style = dict(inherited)
        for key in ("fill", "stroke", "stroke-width", "font-size"):
            if el.get(key) is not None:
                style[key] = el.get(key)
        for key in ("fill", "stroke"):
            if _colour(style[key]) is None:
                return f"a {key} colour this comparison does not evaluate"
        for key, least in (("stroke-width", 0.5), ("font-size", 6.0)):
            m = _PLAIN_NUMBER.fullmatch(style[key])
            if not m:
                return f"a {key} this comparison does not evaluate"
            style[key + ":ok"] = float(m.group(1)) * scale >= least
        fill, stroke = _colour(style["fill"]), _colour(style["stroke"])
        stroked = stroke != "none" and _visible_on_white(stroke) and style["stroke-width:ok"]
        filled = fill != "none" and _visible_on_white(fill)
        if tag in ("line", "polyline", "path"):
            if not stroked or (tag != "line" and fill != "none"):
                return "a line drawn without a plain visible stroke"
        elif tag in ("rect", "circle", "ellipse", "polygon"):
            if not (stroked or filled):
                return "a shape that is not visible"
        elif tag == "text":
            if len(el) or not filled or not style["font-size:ok"]:
                return "a text that is not plainly visible"
        if tag in ("title", "desc", "text"):
            return None if tag == "text" or not len(el) else "a title holding elements"
        for child in el:
            why = walk(child, style, depth + 1)
            if why:
                return why
        return None
    return walk(root, {"fill": "black", "stroke": "none", "stroke-width": "1", "font-size": "16"}, 0)


def _seg_near(p, q, c, r) -> bool:
    """Whether the segment p-q comes within r of the point c."""
    (x1, y1), (x2, y2), (cx, cy) = p, q, c
    dx, dy = x2 - x1, y2 - y1
    t = 0.0 if dx == dy == 0 else max(0.0, min(1.0, ((cx - x1) * dx + (cy - y1) * dy) / (dx * dx + dy * dy)))
    return math.hypot(x1 + t * dx - cx, y1 + t * dy - cy) <= r


def _node_links(drawing) -> tuple[dict, set] | str:
    """(label -> centre, {frozenset of two labels}) of a node-link drawing whose every mark is classified: closed
    shapes as nodes, exactly one text near each as its label (printable, distinct whatever the case), lines whose two
    ends lie on two different nodes and that pass through no other node, everything inside the drawing's area. A
    reason (a str) when a mark is not one of these (C3)."""
    shapes = [s for s in drawing.shapes if s.kind not in ("title", "desc")]
    if len(shapes) > MAX_DRAWING_SHAPES:
        return "the drawing holds more marks than are read"
    w, h = drawing.width, drawing.height
    if not (w > 0 and h > 0):
        return "the drawing has no size"
    for sh in shapes:
        if sh.kind not in ("rect", "circle", "ellipse", "polygon", "line", "polyline", "path", "text"):
            return f"a mark of a kind not read ({clean(sh.kind, 20)})"
        if sh.kind == "path" and str(sh.attrs.get("subpaths", "1")) != "1":
            return "a line made of several pieces"
        if any(not (-1e-6 <= x <= w + 1e-6 and -1e-6 <= y <= h + 1e-6) for x, y in sh.points):
            return "a mark outside the drawing's area"
    nodes = []
    for sh in shapes:
        if sh.kind in ("circle", "ellipse", "rect", "polygon"):
            pts = sh.points[1:] if sh.kind in ("circle", "ellipse") else sh.points
            if len(pts) < 3:
                return "a shape without an area"
            cx = sum(x for x, _ in pts) / len(pts)
            cy = sum(y for _, y in pts) / len(pts)
            r = max(math.hypot(x - cx, y - cy) for x, y in pts)
            if not r > 0:
                return "a shape without an area"
            nodes.append((cx, cy, r))
    if not 2 <= len(nodes) <= 500:
        return "not a drawing of 2 to 500 nodes"
    for (x1, y1, r1), (x2, y2, r2) in itertools.combinations(nodes, 2):
        if math.hypot(x1 - x2, y1 - y2) <= r1 + r2:
            return "two nodes overlap"
    labels: dict[int, str] = {}
    for sh in shapes:
        if sh.kind != "text":
            continue
        if not sh.text or not sh.points:
            return "an empty text"
        x, y = sh.points[0]
        d, i = min((math.hypot(x - cx, y - cy) / r, i) for i, (cx, cy, r) in enumerate(nodes))
        if d > 1.5 or i in labels:
            return "a text that is not the one label of one node"
        label = sh.text.strip()
        if not _LABEL.fullmatch(label):
            return "a label that is not plain"
        labels[i] = label
    if len(labels) != len(nodes) or len({v.casefold() for v in labels.values()}) != len(nodes):
        return "a node without its own label"
    links = set()
    for sh in shapes:
        if sh.kind not in ("line", "polyline", "path"):
            continue
        if len(sh.points) < 2:
            return "a line of one point"
        ends = []
        for x, y in (sh.points[0], sh.points[-1]):
            d, i = min((math.hypot(x - cx, y - cy) / r, i) for i, (cx, cy, r) in enumerate(nodes))
            if d > 1.0:
                return "a line that does not end on a node"
            ends.append(i)
        if ends[0] == ends[1]:
            return "a line from a node to itself"
        for k, (cx, cy, r) in enumerate(nodes):
            if k not in ends and any(_seg_near(p, q, (cx, cy), r) for p, q in zip(sh.points, sh.points[1:])):
                return "a line that passes through another node"
        links.add(frozenset((labels[ends[0]], labels[ends[1]])))
    if not links:
        return "no line joins two nodes"
    return {labels[i]: (n[0], n[1]) for i, n in enumerate(nodes)}, links


def _links_pair(G: Side, D: Side, question: str | None) -> Pair | None:
    """G a node-link drawing (SVG), D a plain text whose every non-empty line joins two of its labels by a relation
    word ("Kofi reports to Tomas", "app -> db")."""
    from . import relate_grammar as rg
    if getattr(G.rep, "kind", None) != "drawing":
        return None
    found = _node_links(G.rep)
    if isinstance(found, str):
        return None                          # not a node-link drawing, or one whose marks are not all read
    centres, drawn = found
    q_read = rg.parse(question or "", rg.GRAMMARS["link"], (G.name, D.name)) if question else None
    what = "the links of a node-link drawing"
    why = _plain_svg(G.raw or b"") if G.fmt == "svg" else "the drawing is not a plain SVG file"
    if why:
        return _unreadable("links", what, "cannot: " + why, q_read)
    read = _plain_lines(D, extra="→–—")
    if read is None:
        return _unreadable("links", what, PLAIN_ONLY, q_read) if D.kind == "document" else None
    lines, whole = read
    patterns = {lab: re.compile(r"(?<![\w-])" + re.escape(lab) + r"(?![\w-])") for lab in centres}
    stated: set = set()
    unread = 0
    for ln in lines:
        if not ln.strip():
            continue
        hits = sorted((m.start(), m.end(), lab) for lab, pat in patterns.items() for m in pat.finditer(ln))
        ok = len(hits) == 2 and hits[0][2] != hits[1][2] and hits[0][1] <= hits[1][0]
        if ok:
            (a0, a1, la), (b0, b1, lb) = hits
            ok = not ln[:a0].strip() and not ln[b1:].strip(" .;") and bool(_LINK_JOIN.match(ln[a1:b0]))
            pair_ = frozenset((la, lb))
            if ok and pair_ in stated:
                ok = False                   # a link named twice
            if ok:
                stated.add(pair_)
        if not ok:
            unread += 1
    if unread or not whole or len(stated) < 2:
        why = (f"{unread} line(s) of the text are not read as one link between two of the drawing's labels"
               if unread else "the text is longer than what is read of it" if not whole else
               "the text names fewer than two links")
        return _unreadable("links", what, "cannot: " + why, q_read)
    # equal sets agree under every reading ("are the links the same", "does the drawing show the text's links", "are
    # the drawing's links in the text"); a disagreement under every reading needs a link missing on each side
    verdict = "consistent" if stated == drawn else "inconsistent" if stated - drawn and drawn - stated else \
        "not_comparable"
    meas = _q("links", "drawing.links", tuple(sorted("-".join(sorted(x)) for x in drawn)),
              how=f"{len(drawn)} line(s) joining two labelled nodes of the drawing", nouns=("link", "line"))
    stmt = _s("links", "document.links", tuple(sorted("-".join(sorted(x)) for x in stated)),
              how=f"{len(stated)} line(s) of text each joining two of the drawing's labels", nouns=("link", "line"))
    note = []
    if stated - drawn:
        note.append(f"{len(stated - drawn)} pair(s) named in the text are not joined in the drawing")
    if drawn - stated:
        note.append(f"{len(drawn - stated)} drawn link(s) are not named in the text")
    pair = Pair(verdict, meas, stmt, len(drawn), len(stated), 0.0, True,
                "the pairs of labels the drawing joins against the pairs the text names (direction not read)",
                "; ".join(note))
    pair.measures = frozenset({"link"})
    pair.complete = True
    pair.read = q_read
    return pair


# ---- a sound whose every stretch is a steady tone or silence (C3) --------------------------------------------------

def _tones_only(sig, tones) -> str | None:
    """None when the sound is steady tones apart from digital silence, the tones separated by quiet gaps; else why
    not: channels that differ (a mono reading would not hear a tone in one channel, or in opposite phase), a quieter
    sound the tone reading does not count, one tone split where its pitch changes, a tone whose loudness is not
    steady (a slow rise or fall: where it "starts" is not plain)."""
    from . import measure_audio as ma
    if not tones or len(tones) > 2000:
        return "the sound holds no tones, or more than are read"
    samples = np.asarray(sig.samples)
    if samples.ndim == 2 and samples.shape[1] > 1 and \
            not np.array_equal(samples, np.repeat(samples[:, :1], samples.shape[1], axis=1)):
        return "the sound's channels differ (only a sound whose channels are the same is read)"
    for (_, end, _), (start, _, _) in zip(tones, tones[1:]):
        if start - end < 0.015:
            return "two tones follow each other without a pause (one sound or two is not known)"
    x = ma.mono(sig).astype(np.float64)
    frame = max(1, int(round(0.005 * float(sig.rate_hz))))
    m = len(x) // frame
    if m < 1:
        return "the sound is too short"
    rms = np.sqrt(np.mean(x[:m * frame].reshape(m, frame) ** 2, axis=1))
    peak = float(np.abs(x).max()) or 1.0
    db = 20 * np.log10(np.maximum(rms, 1e-12))
    loud = db > max(db.max() - 35.0, -60.0)
    covered = np.zeros(m, dtype=bool)
    for start, end, _ in tones:
        # a tone's measured end falls up to one analysis hop (20 ms) before its last sounding frame
        covered[max(0, int(start / 0.005) - 2):min(m, int(math.ceil(end / 0.005)) + 5)] = True
    quiet_sound = (rms > peak * 1e-4) & ~loud & ~covered       # above -80 dB of the peak, not part of a tone
    if np.convolve(quiet_sound.astype(int), np.ones(6, dtype=int), "valid").max(initial=0) >= 6:
        return "the sound holds quieter sound that is not read as a tone (30 ms or more)"
    if (loud & ~covered).any():
        return "the sound holds loud stretches that are not read as tones"
    for start, end, _ in tones:
        part = rms[int(start / 0.005):max(int(start / 0.005) + 1, int(end / 0.005))]
        if len(part) and int((part < np.median(part) * 10 ** (-10 / 20)).sum()) >= 6:
            return "a tone's loudness is not steady (it rises or falls for 30 ms or more)"
    return None


def _times_pair(Sg: Side, D: Side, question: str | None) -> Pair | None:
    """Sg a sound of steady tones, D a plain text whose every non-empty line is an elapsed time (m:ss[.fff]), alone
    or with the sheet's one word for the sound event. The agreement tolerance is TIMES_TOLERANCE_S; a sheet written
    more coarsely agrees only within that, and a time that would agree only if the sheet rounded (or cut off) its
    times is "cannot"."""
    from . import relate_grammar as rg
    onsets = next((f for f in Sg.facts if f.kind == "times" and f.ref == "signal.tone_starts"), None)
    duration = next((f.value for f in Sg.facts if f.ref == "signal.duration"), None)
    if onsets is None or duration is None:
        return None
    read = _plain_lines(D)
    if read is None:
        return None
    lines, whole = read
    stated, unread, words, precision = [], 0, set(), 0.0
    for ln in lines:
        if not ln.strip():
            continue
        m = _TIME_LINE.match(ln)
        if m is None or int(m.group(3)) >= 60 or (m.group(1) and int(m.group(2)) >= 60):
            unread += 1
            continue
        h, mi, sec, frac, word = m.groups()
        if word:
            w = rr.singular(word.lower())
            if w not in _EVENT_WORDS:
                unread += 1
                continue
            words.add(w)
        stated.append((int(h) if h else 0) * 3600 + int(mi) * 60 + int(sec) + (float("0." + frac) if frac else 0.0))
        precision = max(precision, 10.0 ** -len(frac) if frac else 1.0)
    if len(stated) < 2 or (not stated and unread):
        return None
    q_read = rg.parse(question or "", rg.GRAMMARS["time"], (Sg.name, D.name)) if question else None
    what = "a sound's tone onsets against a cue sheet"
    if max(stated) > float(duration) + precision + TIMES_TOLERANCE_S:
        return None                          # not times into this recording (a clock of day, another file)
    why = None
    if unread:
        why = f"{unread} line(s) of the sheet are not an elapsed time with at most one word for the sound"
    elif len(words) > 1:
        why = "the sheet names more than one kind of sound event"
    elif not whole:
        why = "the sheet is longer than what is read of it"
    elif len(set(stated)) != len(stated):
        why = "the sheet lists a time twice"
    else:
        why = _tones_only(Sg.rep, tone_segments(Sg.rep) or [])
    if why:
        return _unreadable("times", what, "cannot: " + why, q_read)
    import bisect
    tol = TIMES_TOLERANCE_S
    measured = sorted(float(t) for t in onsets.value)
    stated = sorted(stated)
    if len(stated) > 2000:
        return _unreadable("times", what, "cannot: the sheet lists more times than are read", q_read)
    low, high = -(precision / 2 + tol), precision + tol   # an onset this far from a listed time
    #                                    would agree if the sheet rounded or cut off its times (the widest reading)

    def far(t, among) -> bool:           # no time of `among` within the widest reading of t
        i = bisect.bisect_left(among, t + low)
        return not (i < len(among) and among[i] < t + high)
    exact = len(measured) == len(stated) and all(abs(m - s) <= tol + REL_EPS for s, m in zip(stated, measured))
    listed_far = any(far(s, measured) for s in stated)
    onset_far = any(far(-m, [-s for s in reversed(stated)]) for m in measured)
    if exact:
        verdict = "consistent"
    elif listed_far and onset_far:
        # a listed time with no onset near it AND an onset with no listed time near it: a disagreement whichever
        # way the question is read ("does each beep start at a listed time", "is there a beep at each listed time")
        verdict = "inconsistent"
    else:
        verdict = "not_comparable"
    worst = max(abs(m - s) for s, m in zip(stated, measured)) if len(measured) == len(stated) else None
    how = (f"each listed time against the measured onset in the same place, within {_fmt(tol)} s (largest "
           f"difference {_fmt(worst)} s)" if worst is not None else
           f"{len(stated)} time(s) listed against {len(measured)} onset(s) measured")
    note = ""
    if verdict == "not_comparable":
        note = ("a listed time would agree only if the sheet rounded or cut off its times, or a time or an onset "
                "has no counterpart on one side only")
    stmt = _s("times", "document.times", tuple(stated), unit=unit_of("s"),
              how="an elapsed time at the start of every line", nouns=("time", "cue"))
    pair = Pair(verdict, onsets, stmt, tuple(measured), tuple(stated), tol, True, how, note, unit="s")
    pair.measures = frozenset({"time"})
    pair.complete = verdict != "not_comparable"
    pair.read = q_read
    return pair


def _structure_pairs(A: Side, B: Side, question: str | None = None) -> list:
    out = []
    for X, Y, flip in ((A, B, False), (B, A, True)):
        made = None
        if X.kind == "bundle" and Y.kind in ("document", "tree"):
            made = _members_pair(X, Y, question)
        elif X.kind == "drawing" and Y.kind == "document":
            made = _links_pair(X, Y, question)
        elif X.kind == "signal" and Y.kind == "document":
            made = _times_pair(X, Y, question)
        if made is not None:
            if flip:
                made = replace(made, a=made.b, b=made.a, a_value=made.b_value, b_value=made.a_value)
            out.append(made)
    return out


# ---- names, mentions, timing -------------------------------------------------------------------------------------------

def shared_names(A: Side, B: Side) -> list[dict]:
    """Names that occur in both (a column, a heading, a node label, a member name): weak evidence of one subject."""
    out = []
    for k in sorted(set(A.names) & set(B.names))[:50]:
        out.append({"name": A.names[k], "first": "name", "second": "name"})
    return out


def mentions(A: Side, B: Side) -> list[dict]:
    """One artifact names the other's file in its own words: it describes it."""
    out = []
    for src, dst, sname in ((A, B, "first"), (B, A, "second")):
        if not dst.name:
            continue
        base = dst.name.rsplit("/", 1)[-1]
        if len(base) < 3:
            continue
        if re.search(rf"(?<![\w.-]){re.escape(base)}(?![\w-])", src.words, re.I):
            out.append({"describes": sname, "named": clean(base, 80)})
    return out


def _times(S: Side):
    for f in S.facts:
        if f.kind == "events":
            ev = list(f.value)
            yield f, [e.seconds for e in ev], all(e.absolute for e in ev)
        elif f.kind == "times":
            yield f, list(f.value), bool(f.get("absolute"))


def timing(A: Side, B: Side) -> dict | None:
    """Two artifacts with time axes: each one's span, and, when both are clock times, the overlap and the offset of
    their starts. Relative times (seconds from a file's start) are compared by span only."""
    ta, tb = next(_times(A), None), next(_times(B), None)
    if ta is None or tb is None or not ta[1] or not tb[1]:
        return None
    fa, xa, abs_a = ta
    fb, xb, abs_b = tb
    out = {"first": {"ref": fa.ref, "start": min(xa), "end": max(xa), "n": len(xa), "clock": abs_a},
           "second": {"ref": fb.ref, "start": min(xb), "end": max(xb), "n": len(xb), "clock": abs_b}}
    if abs_a and abs_b:
        lo, hi = max(min(xa), min(xb)), min(max(xa), max(xb))
        out["overlap_s"] = max(0.0, hi - lo)
        out["start_offset_s"] = min(xb) - min(xa)
    else:
        out["span_s"] = [max(xa) - min(xa), max(xb) - min(xb)]
        out["note"] = "at least one side has no calendar date: only spans and order are meaningful"
    return out


# ---- transfer: units a document states for a table's columns ---------------------------------------------------------

@dataclass
class Transfer:
    table: object                           # the table with the units applied (a new Table; the input is unchanged)
    applied: dict                           # column name -> {"unit", "from", "sentence"}
    absent: list                            # columns no statement gave a unit to
    conflicts: list                         # column already had a different unit: not changed
    ambiguous: list                         # a sentence named the column but not exactly one unit for it

    def describe(self) -> dict:
        d = self.table.describe()
        d["units_from"] = {k: {"unit": v["unit"], "from": v["from"]} for k, v in self.applied.items()}
        return d


def _column_mention(line: str, col: str) -> list[int]:
    pat = re.compile(rf"(?<![\w]){re.escape(col)}(?![\w])", re.I)
    return [m.end() for m in pat.finditer(line)]


_UNIT_PHRASE = re.compile(
    r"(?:\(\s*(?:in\s+)?(?P<paren>[^()]{1,24}?)\s*\)|\b(?:in|unit[s]?\s*(?:is|are|:|=)?|measured\s+in|given\s+in|"
    r"expressed\s+in|recorded\s+in)\s+(?P<word>(?:degrees?\s+)?[A-Za-zµ°%²³$€£]{1,14}(?:\s+(?:celsius|fahrenheit))?)"
    r"(?!\w)(?![/.-]\w)"
    r"|[:|,;—–-]\s*(?P<bare>(?:degrees?\s+)?[A-Za-zµ°%²³$€£]{1,14}(?:\s+(?:celsius|fahrenheit))?)"
    r"(?=\s*(?:[|,;]|\.(?!\w)|$)))",
    re.I)


def _units_in(segment: str) -> list[Unit]:
    out = {}
    for m in _UNIT_PHRASE.finditer(segment):
        text = m.group("paren") or m.group("word") or m.group("bare")
        if re.match(r"\s*(?:per\b|/\s*\w)", segment[m.end():]) or re.search(r"(?:\bper\s+\w+|/\w+)\s*\)?$", text):
            continue                          # a rate ("km per hour", "m/s") is not the unit named alone
        u = unit_of(text)
        if u is not None:
            out[u.symbol] = u
    return list(out.values())


def transfer_units(table, document, *, source: str = "the document", data: bytes | None = None) -> Transfer:
    """Units a document (a README, a data dictionary) states for a table's columns, applied to a copy of the table.
    A unit is applied only when one line or sentence names exactly one of the table's columns and states exactly
    one unit for it; a column that already has a different unit is a conflict and keeps its own; nothing is
    guessed: a column no statement covers stays without a unit. With the document's bytes (`data`, UTF-8 text) its
    own lines are read, since a Markdown paragraph joins lines that each name one column."""
    from .types import Column, Table
    doc = getattr(document, "representation", document)
    text = doc.text() if hasattr(doc, "text") else str(doc)
    if data and b"\x00" not in data[:4096]:
        try:
            text = data[:rr.MAX_TEXT].decode("utf-8")
        except UnicodeDecodeError:
            pass
    lines = []
    for ln in (text or "")[:rr.MAX_TEXT].splitlines():
        for _, sentence in rr.sentences(ln):
            lines += [c for c in re.split(r";\s+", sentence) if c.strip()]
    for b in getattr(doc, "blocks", ()):              # a data dictionary written as a table: one row, one line
        if b.kind == "table" and isinstance(b.ref, Table):
            t = b.ref
            for r in range(min(t.n_rows, 2000)):
                lines.append(" | ".join(str(c.values[r]) for c in t.columns))
    names = [c.name for c in table.columns]
    applied, conflicts, ambiguous = {}, [], []
    for line in lines[:rr.MAX_SENTENCES]:
        hits = [n for n in names if n.strip() and _column_mention(line, n.strip())]
        hits = [n for n in hits if not any(n != o and n.lower() in o.lower() and _column_mention(line, o.strip())
                                           for o in hits)]
        if len(hits) != 1:
            continue
        col = hits[0]
        units = _units_in(line)
        if len(units) != 1:
            if col not in applied:
                ambiguous.append(col)
            continue
        unit = units[0]
        if col in applied and applied[col]["unit"] != unit.symbol:
            ambiguous.append(col)
            applied.pop(col)
            continue
        applied[col] = {"unit": unit.symbol, "from": source, "sentence": clean(line, 160)}
    cols, final = [], {}
    for c in table.columns:
        got = applied.get(c.name)
        if got is None or c.name in ambiguous:
            cols.append(c)
            continue
        own = unit_of(c.unit) if c.unit else None
        if c.unit and (own is None or own.symbol != got["unit"]):
            conflicts.append({"column": c.name, "own": c.unit, "stated": got["unit"]})
            cols.append(c)
            continue
        final[c.name] = got
        cols.append(Column(c.name, c.kind, got["unit"], c.values, c.n_missing))
    absent = [c.name for c in table.columns if c.name not in final and not c.unit]
    return Transfer(Table(tuple(cols), table.n_rows, table.title), final, absent, conflicts,
                    sorted(set(ambiguous) - set(final)))


__all__ = ["Fact", "Pair", "Quantity", "Relation", "Side", "Statement", "Transfer", "compare", "describe_pair",
           "mentions", "own_comments", "quantities", "shared_names", "side", "statements", "timing", "tone_segments",
           "transfer_units"]
