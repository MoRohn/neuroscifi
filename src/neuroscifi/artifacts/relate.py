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
                str(getattr(detected, "format", "") or ""), tuple(getattr(source, "notes", ()) or ()))


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
    if S.fmt in ("markdown", "html", "xhtml", "text", "md") and b"<!--" in raw:
        return True
    if S.fmt == "rtf" and re.search(rb"\\v(?![a-z])", raw):
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
    #                                 answer may become a durable link (`relate_cli.record_links`)

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
        """Machine-readable basis of the overall answer, for a link's `how` (no artifact text in it)."""
        parts = [self.pairs[i].basis() for i in self.decided_by[:4]]
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
    pairs += _structure_pairs(A, B)
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
    strong = [k for k, p in enumerate(pairs) if p.strong]
    named_counts = {n for f in A.facts + B.facts if f.kind == "count" and f.primary for n in f.nouns} & q_words \
        if set(rr.tokens(question or "")) & rr.COUNT_CUES else set()
    structure = [k for k in strong if pairs[k].a.kind in STRUCTURE_KINDS]
    asked = _counts_asked(question or "")
    hidden = [S for S in (A, B) if _hidden_text(S)]
    if question and hidden:
        # lane FX4 (R2): only what a reader sees is evidence; a comment or a hidden group is not, and whether it
        # agrees with what is shown is not known
        rel.reason = ("cannot tell: one of them holds text a reader does not see (a comment or a hidden group); "
                      "hidden text is not evidence, and it is not known whether it agrees with what is shown")
        return rel
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
            decide = [k for k in legacy + structure if _names_measure_and_where(pairs[k], q_tokens)]
            scope = "the comparison the question names, with where it is read from"
        elif not question:
            decide = decide + structure
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
    rel.specific = rel.answer is not None and bool(question) and not scope.startswith("everything")
    return rel


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


# ---- structure across two sides (lane FX3, ADR-081; tightened by lane FX4) ---------------------------------------------
# Three measured comparisons that need both artifacts at once: what one artifact holds is the vocabulary in which the
# other's lines are read (an archive's member names, a drawing's node labels), or the other's lines are elapsed times
# measured against a sound's onsets. Each compares only what was measured in one artifact with what the other states
# line by line, and only lines a reader sees (R2): a plain text file's own lines, otherwise its reader's text. A line
# that cannot be read that way, a listing cut off by a bound, or marks a viewer may not see make it "cannot", never a
# guess. It decides only when the question names its quantity and where it is read from (R1, `compare`).

STRUCTURE_KINDS = ("members", "links", "times")
_DIGESTS = {32: "md5", 40: "sha1", 64: "sha256", 128: "sha512"}
_LIST_MARK = re.compile(r"^\s*(?:[-*+•]|\d{1,4}[.)])\s+")
_HEX_LINE = re.compile(r"^\s*([0-9a-fA-F]{32,128})\s+\*?(\S(?:.*\S)?)\s*$")
_SIZE_FIRST = re.compile(r"^\s*(\d{1,15})\s+(\S(?:.*\S)?)\s*$")
_SIZE_LAST = re.compile(r"^\s*(\S+)\s+(\d{1,15})\s*(?:b|bytes?)?\s*$", re.I)
_ELAPSED = re.compile(r"^\s*(?:(\d{1,2}):)?(\d{1,2}):(\d{2}(?:[.,]\d{1,6})?)(?![\d:])")
_PATHLIKE = re.compile(r"^[^\s:]*(?:/[^\s/]+|\.[A-Za-z0-9]{1,8})$")
SIZE_KEYS = frozenset("bytes byte size sizes length octets".split())
MAX_LISTING_LINES = 20_000
MAX_DIGEST_BYTES = 256 << 20            # member bytes hashed per comparison, at most (as max_decoded_bytes)
TIMES_TOLERANCE_S = 0.05                # ten 5 ms analysis frames of `tone_segments`
# R1/R4 (lane FX4): the words a link line may hold between two labels. A line naming two labels joined by anything
# else ("is unrelated to", "stopped reporting to", "and", a distance) is not read as a link, and the comparison is
# then "cannot". General English verbs of a directed or undirected relation; the direction itself is not read.
_LINK_JOIN = re.compile(
    r"^\s*(?:->|-->|→|—>|<->|--|–|—|-|to|reports? to|reports? into|depends? on|feeds?(?: into| to)?|leads? to|links? to|"
    r"is linked to|connects? to|is connected to|calls|is a child of|is the child of|is a parent of|is the parent of|"
    r"precedes|follows|flows? (?:in)?to|sends? to|hands? over to|passes to|points to|manages|is managed by|"
    r"uses|imports|includes)\s*$", re.I)
# lines of a cue sheet that name an end, not an onset: the sheet then lists more than onsets (R4)
_END_WORDS = frozenset("stop stops stopped end ends ended off finish finishes finished release releases".split())
_INVISIBLE = re.compile(rb"(?:stroke-opacity|opacity|fill-opacity)\s*[=:]\s*[\"']?\s*0(?:\.0*)?(?![.\d])|stroke\s*[=:]\s*"
                        rb"[\"']?\s*(?:none|transparent)|stroke-width\s*[=:]\s*[\"']?\s*0(?:\.0*)?(?![.\d])|"
                        rb"visibility\s*[=:]\s*[\"']?\s*hidden|display\s*[=:]\s*[\"']?\s*none", re.I)


def _text_lines(S: Side) -> tuple[list[str], bool]:
    """(the lines a reader sees, whether all of them were read). A plain text file's own lines; for every other
    document (Markdown, DOCX, PDF, HTML, RTF) its reader's text, never its raw bytes (R2). List markers dropped."""
    rep = S.rep
    text = ""
    if S.fmt == "text" and S.raw and b"\x00" not in S.raw[:4096]:
        from .adapters.plain import decode_text
        try:
            text = decode_text(bytes(S.raw))[0]
        except Exception:
            text = ""
    if not text:
        try:
            text = rep.text() if getattr(rep, "kind", None) == "document" else ""
        except Exception:
            text = ""
    text = text or ""
    lines = text[:rr.MAX_TEXT].splitlines()
    complete = len(text) < rr.MAX_TEXT and len(lines) <= MAX_LISTING_LINES
    return [_LIST_MARK.sub("", ln) for ln in lines[:MAX_LISTING_LINES]], complete


def _member_table(B: Side) -> dict:
    """{member path (as a question would name it): (size, member)} of a decoded archive; {} when a name repeats."""
    from .outcome import member_path
    out = {}
    for m in getattr(B.rep, "members", ()) or ():
        if getattr(m.detected, "format", None) == "directory":
            continue
        path = member_path(str(m.path))
        if not path or path in out:
            return {}
        out[path] = (int(m.size), m)
    return out


def _listing_from_lines(lines: list[str]) -> tuple[list[tuple[str, str, object]], int]:
    """((path, property, value) per listed line, lines not read): "<hex digest>  <path>" (sha256sum and kin),
    "<size>  <path>" or "<path>  <size>" (bytes). A non-empty line that is not one of these, or whose name is not a
    path ("total 916", "Files: 3"), is a line not read."""
    out, unread = [], 0
    for ln in lines:
        if not ln.strip():
            continue
        m = _HEX_LINE.match(ln)
        if m and len(m.group(1)) in _DIGESTS and _PATHLIKE.match(m.group(2).strip()):
            out.append((m.group(2).strip(), _DIGESTS[len(m.group(1))], m.group(1).lower()))
            continue
        m = _SIZE_FIRST.match(ln)
        if m and _PATHLIKE.match(m.group(2).strip()) and not rr.is_number_like(m.group(2)):
            out.append((m.group(2).strip(), "size", int(m.group(1))))
            continue
        m = _SIZE_LAST.match(ln)
        if m and _PATHLIKE.match(m.group(1).strip()) and not rr.is_number_like(m.group(1)):
            out.append((m.group(1).strip(), "size", int(m.group(2))))
            continue
        unread += 1
    return out, unread


def _listing_from_tree(tree) -> tuple[list[tuple[str, str, object]], int]:
    """A JSON or XML list of records, each a path and a size in bytes ({"path": ..., "bytes": ...})."""
    from .types import _walk
    out = []
    for node, _ in _walk(tree.root, limit=200_000):
        kids = getattr(node, "children", ()) or ()
        if not kids or len(kids) > 16:
            continue
        texts = [c for c in kids if isinstance(c.value, str) and _PATHLIKE.match(c.value.strip())
                 and not rr.is_number_like(c.value)]
        sizes = [c for c in kids if str(c.name).lower() in SIZE_KEYS and not isinstance(c.value, bool)
                 and (isinstance(c.value, (int, float)) or isinstance(c.value, str) and c.value.isdigit())]
        if len(texts) == 1 and len(sizes) == 1:
            out.append((texts[0].value.strip(), "size", int(float(sizes[0].value))))
    return out, 0


def _digests(B: Side, wanted: dict) -> dict:
    """{path: hex digest} of the listed ZIP members, each read once by ONE safe reader built once (linear in the
    members and bounded by MAX_DIGEST_BYTES in all); a member that cannot be read is left out."""
    import hashlib
    if not B.raw:
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


def _members_pair(B: Side, D: Side) -> Pair | None:
    """B an archive, D a listing of its members with a size or a digest each."""
    table = _member_table(B)
    if len(table) < 1:
        return None
    if getattr(D.rep, "kind", None) == "tree":
        listed, unread = _listing_from_tree(D.rep)
        whole = True
    else:
        lines, whole = _text_lines(D)
        listed, unread = _listing_from_lines(lines)
    if len(listed) < 1 or len({p for p, _, _ in listed}) != len(listed):
        return None
    from .outcome import member_path
    matched = [p for p, _, _ in listed if member_path(p) in table]
    if not matched or len(matched) * 2 < len(listed):
        return None                         # not a listing of this archive
    digests = _digests(B, {member_path(p): prop for p, prop, _ in listed if prop != "size"
                           and member_path(p) in table})
    agree, disagree, absent, unhashed = [], [], [], 0
    for path, prop, value in ((member_path(p), prop, v) for p, prop, v in listed):
        if path not in table:
            absent.append(path)
            continue
        got = table[path][0] if prop == "size" else digests.get(path)
        if got is None:
            unhashed += 1
            continue
        (agree if got == value else disagree).append(path)
    props = sorted({prop for _, prop, _ in listed})
    what = "sizes in bytes" if props == ["size"] else " and ".join(p.upper() if p != "size" else "sizes"
                                                                    for p in props)
    meas = _q("members", "bundle.members.listing", tuple(sorted(table)), how=f"each member's {what}, measured "
              f"from its bytes", nouns=("member", "file"))
    stmt = _s("members", "document.listing", tuple(p for p, _, _ in listed), how=f"{len(listed)} line(s) listing "
              f"a member with its {what}", nouns=("member", "file"))
    note = []
    unlisted = len(set(table) - {member_path(p) for p, _, _ in listed})
    if absent:
        note.append(f"{len(absent)} listed member(s) are not in the archive")
    if unlisted:
        note.append(f"{unlisted} member(s) of the archive are not listed")
    if unhashed:
        note.append(f"{unhashed} member(s) could not be read again to compute a digest")
    if unread:
        note.append(f"{unread} line(s) of the listing could not be read as a member and its value")
    if not whole:
        note.append("the listing is longer than what is read of it: its end was not read")
    if not whole or unread:
        verdict = "not_comparable"          # R4/R5: a partly read listing never answers either way
    elif disagree or absent:
        verdict = "inconsistent"
    elif unhashed or unlisted or not agree:
        verdict = "not_comparable"
    else:
        verdict = "consistent"
    pair = Pair(verdict, meas, stmt, len(agree), len(listed), 0.0, True,
                f"each listed member's {what} against the archive's member ({len(agree)} of {len(listed)} agree)",
                "; ".join(note))
    pair.measures = frozenset({"size"} if props == ["size"] else {"digest"} if "size" not in props
                              else {"size", "digest"})
    pair.complete = whole and not unread and not unlisted
    return pair


def _node_links(drawing, raw: bytes | None) -> tuple[dict, set] | None:
    """(label -> centre, {frozenset of two labels}) of a node-link drawing: closed shapes as nodes, each with exactly
    one text near it as its label, and lines or paths whose two ends lie on two different nodes. None unless every
    line joins two nodes and every node has its own label (a chart, a map or a picture is not read as one), and
    None when the drawing holds marks a viewer may not see (zero opacity or width, no stroke, hidden: R2)."""
    if raw and _INVISIBLE.search(raw):
        return None
    nodes = []
    for sh in drawing.shapes:
        if sh.kind in ("circle", "ellipse", "rect", "polygon") and len(sh.points) >= 3:
            pts = sh.points[1:] if sh.kind in ("circle", "ellipse") else sh.points
            cx = sum(x for x, _ in pts) / len(pts)
            cy = sum(y for _, y in pts) / len(pts)
            r = max(math.hypot(x - cx, y - cy) for x, y in pts)
            if r > 0:
                nodes.append((cx, cy, r))
    if not 2 <= len(nodes) <= 500:
        return None
    labels: dict[int, str] = {}
    for sh in drawing.shapes:
        if sh.kind != "text" or not sh.text or not sh.points:
            continue
        x, y = sh.points[0]
        d, i = min((math.hypot(x - cx, y - cy) / r, i) for i, (cx, cy, r) in enumerate(nodes))
        if d > 3.0:
            continue                         # a title or a note, not a node's label
        if i in labels:
            return None
        labels[i] = clean(sh.text, 80).strip()
    if len(labels) != len(nodes) or len(set(labels.values())) != len(nodes) or not all(labels.values()):
        return None
    links = set()
    for sh in drawing.shapes:
        if sh.kind not in ("line", "polyline", "path") or len(sh.points) < 2:
            continue
        ends = []
        for x, y in (sh.points[0], sh.points[-1]):
            d, i = min((math.hypot(x - cx, y - cy) / r, i) for i, (cx, cy, r) in enumerate(nodes))
            if d > 1.5:
                return None                  # a line that does not end on a node: not a node-link drawing
            ends.append(i)
        if ends[0] == ends[1]:
            return None
        links.add(frozenset((labels[ends[0]], labels[ends[1]])))
    if not links:
        return None
    centres = {labels[i]: (n[0], n[1]) for i, n in enumerate(nodes)}
    return centres, links


def _links_pair(G: Side, D: Side) -> Pair | None:
    """G a node-link drawing, D text whose lines each join two of its labels by a relation word ("Kofi reports to
    Tomas", "app -> db"). Any line naming a label that is not exactly that makes the comparison "cannot"."""
    found = _node_links(G.rep, G.raw) if getattr(G.rep, "kind", None) == "drawing" else None
    if found is None:
        return None
    centres, drawn = found
    lines, whole = _text_lines(D)
    if not whole:
        return None
    patterns = {lab: re.compile(r"(?<![\w-])" + re.escape(lab) + r"(?![\w-])") for lab in centres}
    stated, nonempty, used = set(), 0, 0
    for ln in lines:
        if not ln.strip():
            continue
        nonempty += 1
        hits = sorted((m.start(), m.end(), lab) for lab, pat in patterns.items() for m in pat.finditer(ln))
        if not hits:
            continue
        if len(hits) != 2 or hits[0][2] == hits[1][2]:
            return None                      # one label, three or more, or one twice: not read
        (a0, a1, la), (b0, b1, lb) = hits
        if ln[:a0].strip() or ln[b1:].strip(" .;") or not _LINK_JOIN.match(ln[a1:b0]):
            return None                      # words around the labels, or a join that is not a relation
        stated.add(frozenset((la, lb)))
        used += 1
    if len(stated) < 2:
        return None
    complete = used == nonempty
    if stated == drawn:
        verdict = "consistent"
    elif stated - drawn or complete:
        verdict = "inconsistent"
    else:
        verdict = "not_comparable"
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
    pair.complete = complete
    return pair


def _times_pair(Sg: Side, D: Side) -> Pair | None:
    """Sg a sound whose tone onsets were measured, D lines that each begin with an elapsed time (m:ss.fff) of an
    onset. The tolerance is the larger of TIMES_TOLERANCE_S and half the written precision (a time written in whole
    seconds is rounded); one that agrees only if the sheet truncated its times is "cannot" (R4)."""
    onsets = next((f for f in Sg.facts if f.kind == "times" and f.ref == "signal.tone_starts"), None)
    duration = next((f.value for f in Sg.facts if f.ref == "signal.duration"), None)
    if onsets is None or duration is None:
        return None
    lines, whole = _text_lines(D)
    if not whole:
        return None
    stated, nonempty, precision = [], 0, 0.0
    for ln in lines:
        if not ln.strip():
            continue
        nonempty += 1
        m = _ELAPSED.match(ln)
        if m is None or set(rr.tokens(ln[m.end():])) & _END_WORDS:
            return None                      # a line that is not an onset's time
        h, mi, sec = m.groups()
        sec = sec.replace(",", ".")
        stated.append((int(h) if h else 0) * 3600 + int(mi) * 60 + float(sec))
        frac = sec.split(".")[1] if "." in sec else ""
        precision = max(precision, 10.0 ** -len(frac) if frac else 1.0)
    if len(stated) < 2 or len(stated) != nonempty:
        return None
    if max(stated) > float(duration) + max(TIMES_TOLERANCE_S, precision):
        return None                          # not times into this recording (a clock of day, another file)
    tol = max(TIMES_TOLERANCE_S, precision / 2)
    measured = sorted(float(t) for t in onsets.value)
    stated = sorted(stated)
    if len(measured) != len(stated):
        verdict = "inconsistent"
        how = f"{len(stated)} time(s) listed against {len(measured)} onset(s) measured"
    else:
        worst = max(abs(a - b) for a, b in zip(stated, measured))
        if worst <= tol + REL_EPS:
            verdict = "consistent"
        elif precision > TIMES_TOLERANCE_S and all(-REL_EPS <= m - s < precision + REL_EPS
                                                     for s, m in zip(stated, measured)):
            verdict = "not_comparable"       # agrees only if the sheet cut its times off instead of rounding
        else:
            verdict = "inconsistent"
        how = f"each listed time against the measured onset in the same place, within {_fmt(tol)} s " \
              f"(largest difference {_fmt(worst)} s)"
    stmt = _s("times", "document.times", tuple(stated), unit=unit_of("s"),
              how="an elapsed time at the start of every line", nouns=("time", "cue"))
    pair = Pair(verdict, onsets, stmt, tuple(measured), tuple(stated), tol, True, how, unit="s")
    pair.measures = frozenset({"time"})
    pair.complete = True
    return pair


def _structure_pairs(A: Side, B: Side) -> list:
    out = []
    for X, Y, flip in ((A, B, False), (B, A, True)):
        made = None
        if X.kind == "bundle" and Y.kind in ("document", "tree"):
            made = _members_pair(X, Y)
        elif X.kind == "drawing" and Y.kind == "document":
            made = _links_pair(X, Y)
        elif X.kind == "signal" and Y.kind == "document":
            made = _times_pair(X, Y)
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
