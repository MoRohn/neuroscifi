"""The native representations of OMNIMIND artifacts (DESIGN §2, ADR-072).

Frozen dataclasses with no behaviour beyond what DESIGN §2 states: every representation has a class attribute
`kind`, `describe()` (a JSON-able dict of structure and measurements only, at most 32 KiB when serialised, never an
invented meaning), `shape()` (a `ContentShape` for the form planner, with no reference to the bytes) and `text()`
(the artifact's own words, bounded; "" when it has none; always untrusted).

Construction checks the stated invariants (dtypes, dimensions, ranges, closed vocabularies) and raises `Malformed`
when they do not hold, so a decoder fed hostile bytes can never hand on a representation that breaks its contract.
Classes holding numpy arrays compare by identity (`eq=False`): element-wise array equality has no single truth value.

Importable with the standard library and numpy only.
"""
from __future__ import annotations

import json
import math
from collections.abc import Mapping
from dataclasses import dataclass, field

import numpy as np

FAMILIES = ("text", "document", "table", "tree", "image", "drawing", "audio", "video", "mesh", "geo",
            "array", "code", "notebook", "database", "archive", "unknown")
BASES = ("signature", "structure", "declared", "none")
SHAPE_KINDS = ("scalar", "series", "categories", "distribution", "relation", "hierarchy", "network", "sequence",
               "timeline", "process", "surface", "solid", "map", "image", "sound", "prose", "code", "records")
COLUMN_KINDS = ("number", "integer", "boolean", "time", "text")
RASTER_MODES = {"gray": (2, 1), "rgb": (3, 3), "rgba": (3, 4)}   # mode -> (ndim, channels)
BLOCK_KINDS = ("heading", "paragraph", "list_item", "table", "figure", "code", "equation", "page_break", "note")
ERROR_REASONS = ("too_large", "malformed", "unsupported", "refused", "missing_capability", "timeout")

DESCRIBE_MAX_BYTES = 32 * 1024   # describe() serialised as compact JSON never exceeds this
TEXT_MAX_CHARS = 1 << 20         # text() never returns more characters than this
LIST_SAMPLE = 32                 # describe() lists at most this many items of any collection
STRING_SAMPLE = 120              # describe() cuts every string it quotes from the content to this many characters


# ---------------------------------------------------------------------------------------------------------------
# Errors


class ArtifactError(ValueError):
    """Every failure an OMNIMIND lane raises derives from this. `reason` is a stable machine word (ERROR_REASONS);
    the message is a plain sentence that never quotes the input's content."""

    reason = "malformed"

    def __init__(self, message: str, *, reason: str | None = None):
        super().__init__(message)
        if reason is not None:
            if reason not in ERROR_REASONS:
                raise ValueError(f"unknown error reason {reason!r}")
            self.reason = reason


class Unsupported(ArtifactError):
    """Recognised, but nothing here can decode it. `needs` names what would have to exist."""

    reason = "unsupported"

    def __init__(self, message: str, *, needs: tuple[str, ...] = (), reason: str | None = None):
        super().__init__(message, reason=reason)
        self.needs = tuple(needs)


class Malformed(ArtifactError):
    reason = "malformed"


class Refused(ArtifactError):
    """A safety limit or rule said no. `rule` names it ("max_bytes", "pickle", "xml_dtd", ...)."""

    reason = "refused"

    def __init__(self, message: str, *, rule: str, reason: str | None = None):
        super().__init__(message, reason=reason)
        self.rule = rule


# ---------------------------------------------------------------------------------------------------------------
# Detection, limits, shape


@dataclass(frozen=True)
class Detected:
    family: str                 # one of FAMILIES
    format: str                 # short lower-case name: "png", "wav", "csv", "docx", ...; "unknown" if not identified
    media_type: str             # best IANA type, "application/octet-stream" if unknown
    basis: str                  # "signature" | "structure" | "declared" | "none": what the identification rests on
    container: str | None       # "zip", "riff", "gzip", "ole", "isobmff", "ebml", ... when a member of one
    reasons: tuple[str, ...]    # plain statements of what was seen

    def __post_init__(self):
        if self.family not in FAMILIES:
            raise Malformed(f"unknown family {self.family!r}")
        if self.basis not in BASES:
            raise Malformed(f"unknown basis {self.basis!r}")
        if not isinstance(self.reasons, tuple):
            object.__setattr__(self, "reasons", tuple(self.reasons))


@dataclass(frozen=True)
class Limits:
    """Every decoder honours these. Defaults are the conservative values of DESIGN §2."""

    max_bytes: int = 64 << 20           # input size
    max_decoded_bytes: int = 256 << 20  # total bytes any decoder may materialise
    max_members: int = 4096             # archive members, sheets, pages, frames, tracks
    max_depth: int = 4                  # nesting of containers
    max_ratio: int = 200                # decompressed/compressed, per stream
    max_elements: int = 5_000_000       # cells, XML nodes, vertices (pixels are bounded by max_decoded_bytes)
    max_seconds: float = 20.0           # wall clock per decode (checked cooperatively)


@dataclass(frozen=True)
class ContentShape:
    """What a planner needs to know about content, with no reference to its bytes."""

    kinds: tuple[str, ...]
    n_items: int
    n_series: int
    has_time: bool
    has_space: int              # 0, 2 or 3
    has_parameters: bool
    duration_s: float
    text_chars: int

    def __post_init__(self):
        bad = [k for k in self.kinds if k not in SHAPE_KINDS]
        if bad:
            raise Malformed(f"unknown content kind(s) {bad!r}")
        if self.has_space not in (0, 2, 3):
            raise Malformed("has_space must be 0, 2 or 3")
        if self.n_items < 0 or self.n_series < 0 or self.text_chars < 0:
            raise Malformed("counts must not be negative")
        if not (math.isfinite(self.duration_s) and self.duration_s >= 0):
            raise Malformed("duration_s must be a finite number >= 0")


def _shape(kinds, n_items=0, n_series=0, has_time=False, has_space=0, has_parameters=False, duration_s=0.0,
           text_chars=0) -> ContentShape:
    return ContentShape(tuple(dict.fromkeys(kinds)), int(n_items), int(n_series), bool(has_time), int(has_space),
                        bool(has_parameters), float(duration_s), int(text_chars))


# ---------------------------------------------------------------------------------------------------------------
# describe() helpers: JSON-able, bounded, quoting only what is there


def _num(value):
    """A JSON-able number, or None for NaN/infinite (JSON has no such numbers)."""
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    if isinstance(value, (int, np.integer)):
        return int(value)
    value = float(value)
    return value if math.isfinite(value) else None


def _cut(text, limit: int = STRING_SAMPLE):
    if text is None:
        return None
    text = str(text)
    return text if len(text) <= limit else text[:limit] + "…"


def _json_size(value) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8"))


def _shrink(value, budget: int):
    """Cut strings to `budget` characters and lists to `budget` items, recursively (a fallback bound)."""
    if isinstance(value, str):
        return _cut(value, budget)
    if isinstance(value, list):
        out = [_shrink(v, budget) for v in value[:budget]]
        if len(value) > budget:
            out.append({"omitted": len(value) - budget})
        return out
    if isinstance(value, dict):
        return {_cut(k, budget): _shrink(v, budget) for k, v in list(value.items())[:max(budget, 8)]}
    return value


def bounded(description: dict) -> dict:
    """`description` itself when it serialises to at most DESCRIBE_MAX_BYTES, else a progressively shrunk copy
    marked `"truncated": true`. The result is always JSON-able (NaN and infinities become null)."""
    description = _jsonable(description)
    if _json_size(description) <= DESCRIBE_MAX_BYTES:
        return description
    for budget in (64, 32, 16, 8, 4, 2):
        shrunk = _shrink(description, budget)
        shrunk["truncated"] = True
        if _json_size(shrunk) <= DESCRIBE_MAX_BYTES:
            return shrunk
    return {"kind": _cut(description.get("kind"), 32), "truncated": True}


def _jsonable(value, depth: int = 0):
    if depth > 32:
        return None
    if value is None or isinstance(value, str):
        return value
    if isinstance(value, (bool, int, float, np.generic)):
        return _num(value)
    if isinstance(value, Mapping):
        return {str(k): _jsonable(v, depth + 1) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v, depth + 1) for v in value]
    return _cut(type(value).__name__)


def _value_sample(value):
    """A property/attribute value as it may appear in describe(): scalars verbatim (strings cut), else its type."""
    if value is None or isinstance(value, (bool, int, float, np.generic)):
        return _num(value) if value is not None else None
    if isinstance(value, str):
        return _cut(value)
    return {"type": type(value).__name__}


def _bounded_text(parts, limit: int = TEXT_MAX_CHARS) -> str:
    out, total = [], 0
    for part in parts:
        if not part:
            continue
        part = str(part)
        if total + len(part) > limit:
            out.append(part[:max(0, limit - total)])
            break
        out.append(part)
        total += len(part) + 1
    return "\n".join(out)[:limit]


def _array(value, name: str, *, dtypes, ndim) -> np.ndarray:
    if not isinstance(value, np.ndarray):
        raise Malformed(f"{name} must be a numpy array")
    if value.dtype not in dtypes:
        raise Malformed(f"{name} has dtype {value.dtype}, expected one of {[str(d) for d in dtypes]}")
    if value.ndim not in ndim:
        raise Malformed(f"{name} has {value.ndim} dimension(s), expected {ndim}")
    return value


def _strings(value, name: str) -> tuple:
    value = tuple(value)
    if not all(isinstance(v, str) for v in value):
        raise Malformed(f"{name} must hold only strings")
    return value


def _stats(values: np.ndarray) -> dict:
    finite = values[np.isfinite(values)] if values.size else values
    if not finite.size:
        return {"min": None, "max": None}
    return {"min": _num(finite.min()), "max": _num(finite.max())}


# ---------------------------------------------------------------------------------------------------------------
# Table


@dataclass(frozen=True, eq=False)
class Column:
    name: str
    kind: str                   # "number" | "integer" | "boolean" | "time" | "text"
    unit: str | None
    values: np.ndarray | tuple[str, ...]
    n_missing: int

    def __post_init__(self):
        if self.kind not in COLUMN_KINDS:
            raise Malformed(f"unknown column kind {self.kind!r}")
        if self.kind == "text":
            object.__setattr__(self, "values", _strings(self.values, f"column {self.name!r}"))
        else:
            dtypes = {"number": (np.float64,), "time": (np.float64,), "integer": (np.int64, np.float64),
                      "boolean": (np.bool_, np.float64)}[self.kind]
            _array(self.values, f"column {self.name!r}", dtypes=tuple(np.dtype(d) for d in dtypes), ndim=(1,))
        if not 0 <= self.n_missing <= len(self.values):
            raise Malformed("n_missing must lie between 0 and the number of values")

    def describe(self) -> dict:
        out = {"name": _cut(self.name), "kind": self.kind, "unit": _cut(self.unit), "n_values": len(self.values),
               "n_missing": int(self.n_missing)}
        if self.kind == "text":
            distinct = list(dict.fromkeys(self.values))
            out["n_distinct"] = len(distinct)
            out["sample"] = [_cut(v) for v in distinct[:8]]
        elif self.kind == "boolean":
            arr = np.asarray(self.values, dtype=np.float64)
            out["n_true"] = int(np.nansum(arr == 1))
        else:
            out.update(_stats(np.asarray(self.values, dtype=np.float64)))
        return out


@dataclass(frozen=True, eq=False)
class Table:
    kind = "table"
    columns: tuple[Column, ...]
    n_rows: int
    title: str | None = None

    def __post_init__(self):
        object.__setattr__(self, "columns", tuple(self.columns))
        if self.n_rows < 0:
            raise Malformed("n_rows must not be negative")
        for column in self.columns:
            if not isinstance(column, Column):
                raise Malformed("a table holds only Column objects")
            if len(column.values) != self.n_rows:
                raise Malformed(f"column {column.name!r} has {len(column.values)} values, the table {self.n_rows} rows")

    def describe(self) -> dict:
        return bounded({"kind": self.kind, "title": _cut(self.title), "n_rows": self.n_rows,
                        "n_columns": len(self.columns),
                        "columns": [c.describe() for c in self.columns[:LIST_SAMPLE]]})

    def shape(self) -> ContentShape:
        numeric = [c for c in self.columns if c.kind in ("number", "integer")]
        kinds = ["records"]
        if any(c.kind == "time" for c in self.columns) and numeric:
            kinds.append("series")
        if any(c.kind == "text" for c in self.columns) and numeric:
            kinds.append("categories")
        if len(self.columns) == 1 and numeric:
            kinds.append("distribution")
        return _shape(kinds, n_items=self.n_rows, n_series=len(numeric),
                      has_time=any(c.kind == "time" for c in self.columns), text_chars=len(self.text()))

    def text(self) -> str:
        parts = [self.title] if self.title else []
        parts.append("\t".join(c.name for c in self.columns))
        for c in self.columns:
            if c.kind == "text":
                parts.extend(c.values)
        return _bounded_text(parts)


# ---------------------------------------------------------------------------------------------------------------
# Sampled media


@dataclass(frozen=True, eq=False)
class Signal:
    kind = "signal"
    samples: np.ndarray         # float32 in [-1, 1], shape (n, channels)
    rate_hz: float
    channel_names: tuple[str, ...]

    def __post_init__(self):
        _array(self.samples, "samples", dtypes=(np.dtype(np.float32),), ndim=(2,))
        object.__setattr__(self, "channel_names", _strings(self.channel_names, "channel_names"))
        if len(self.channel_names) != self.samples.shape[1]:
            raise Malformed("one channel name per channel")
        if not (math.isfinite(self.rate_hz) and self.rate_hz > 0):
            raise Malformed("rate_hz must be a positive finite number")
        if self.samples.size and not (np.isfinite(self.samples).all() and np.abs(self.samples).max() <= 1.0):
            raise Malformed("samples must be finite and lie in [-1, 1]")

    def describe(self) -> dict:
        n, c = self.samples.shape
        out = {"kind": self.kind, "n_samples": n, "channels": c, "rate_hz": _num(self.rate_hz),
               "duration_s": _num(n / self.rate_hz), "channel_names": [_cut(x) for x in self.channel_names[:LIST_SAMPLE]]}
        if n:
            out["peak"] = _num(np.abs(self.samples).max())
            out["rms"] = [_num(v) for v in np.sqrt(np.mean(self.samples.astype(np.float64) ** 2, axis=0))[:LIST_SAMPLE]]
        return bounded(out)

    def shape(self) -> ContentShape:
        n, c = self.samples.shape
        kinds = ("sound", "series") if self.rate_hz >= 8000 else ("series",)
        return _shape(kinds, n_items=n, n_series=c, has_time=True, duration_s=n / self.rate_hz)

    def text(self) -> str:
        return ""


@dataclass(frozen=True, eq=False)
class Raster:
    kind = "raster"
    pixels: np.ndarray          # (h, w) or (h, w, c), uint8 or uint16
    mode: str                   # "gray" | "rgb" | "rgba"
    bits: int

    def __post_init__(self):
        if self.mode not in RASTER_MODES:
            raise Malformed(f"unknown raster mode {self.mode!r}")
        _array(self.pixels, "pixels", dtypes=(np.dtype(np.uint8), np.dtype(np.uint16)), ndim=(2, 3))
        ndim, channels = RASTER_MODES[self.mode]
        if self.pixels.ndim != ndim or (ndim == 3 and self.pixels.shape[2] != channels):
            raise Malformed(f"pixels of shape {self.pixels.shape} do not fit mode {self.mode!r}")
        if not 1 <= self.bits <= self.pixels.dtype.itemsize * 8:
            raise Malformed(f"bits {self.bits} do not fit dtype {self.pixels.dtype}")

    @property
    def size(self) -> tuple[int, int]:
        return int(self.pixels.shape[1]), int(self.pixels.shape[0])

    def describe(self) -> dict:
        w, h = self.size
        out = {"kind": self.kind, "width": w, "height": h, "mode": self.mode, "bits": self.bits,
               "dtype": str(self.pixels.dtype)}
        if self.pixels.size:
            # measured in the samples' own type (the mean accumulates in float64): a float64 copy of every sample
            # would be 8 bytes per sample outside any decode budget (lane B2's finding: 288 MiB for 12 MP RGB)
            flat = self.pixels.reshape(h * w, -1)
            out["channel_mean"] = [_num(v) for v in flat.mean(axis=0, dtype=np.float64)]
            out["channel_min"] = [_num(v) for v in flat.min(axis=0)]
            out["channel_max"] = [_num(v) for v in flat.max(axis=0)]
        return bounded(out)

    def shape(self) -> ContentShape:
        w, h = self.size
        channels = 1 if self.pixels.ndim == 2 else self.pixels.shape[2]
        return _shape(("image",), n_items=w * h, n_series=channels, has_space=2)

    def text(self) -> str:
        return ""


@dataclass(frozen=True, eq=False)
class Frames:
    kind = "frames"
    frames: tuple[Raster, ...]
    delays_s: tuple[float, ...]

    def __post_init__(self):
        object.__setattr__(self, "frames", tuple(self.frames))
        object.__setattr__(self, "delays_s", tuple(float(d) for d in self.delays_s))
        if not all(isinstance(f, Raster) for f in self.frames):
            raise Malformed("frames must be Raster objects")
        if len(self.delays_s) != len(self.frames):
            raise Malformed("one delay per frame")
        if not all(math.isfinite(d) and d >= 0 for d in self.delays_s):
            raise Malformed("delays must be finite and >= 0")

    def describe(self) -> dict:
        first = self.frames[0].size if self.frames else (0, 0)
        return bounded({"kind": self.kind, "n_frames": len(self.frames), "width": first[0], "height": first[1],
                        "duration_s": _num(sum(self.delays_s)),
                        "delays_s": [_num(d) for d in self.delays_s[:LIST_SAMPLE]]})

    def shape(self) -> ContentShape:
        return _shape(("image", "sequence"), n_items=len(self.frames), n_series=1, has_time=True, has_space=2,
                      duration_s=sum(self.delays_s))

    def text(self) -> str:
        return ""


# ---------------------------------------------------------------------------------------------------------------
# Vector graphics


@dataclass(frozen=True)
class Shape:
    kind: str
    points: tuple[tuple[float, float], ...]
    text: str | None
    attrs: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self):
        object.__setattr__(self, "points", tuple((float(x), float(y)) for x, y in self.points))


@dataclass(frozen=True)
class Drawing:
    kind = "drawing"
    width: float
    height: float
    shapes: tuple[Shape, ...]

    def __post_init__(self):
        object.__setattr__(self, "shapes", tuple(self.shapes))
        if not all(isinstance(s, Shape) for s in self.shapes):
            raise Malformed("a drawing holds only Shape objects")
        if not (math.isfinite(self.width) and math.isfinite(self.height) and self.width >= 0 and self.height >= 0):
            raise Malformed("width and height must be finite and >= 0")

    def describe(self) -> dict:
        counts: dict[str, int] = {}
        for s in self.shapes:
            counts[s.kind] = counts.get(s.kind, 0) + 1
        texts = [s.text for s in self.shapes if s.text]
        return bounded({"kind": self.kind, "width": _num(self.width), "height": _num(self.height),
                        "n_shapes": len(self.shapes), "shape_counts": dict(list(counts.items())[:LIST_SAMPLE]),
                        "n_texts": len(texts), "text_sample": [_cut(t) for t in texts[:8]]})

    def shape(self) -> ContentShape:
        return _shape(("image",), n_items=len(self.shapes), has_space=2, text_chars=len(self.text()))

    def text(self) -> str:
        return _bounded_text(s.text for s in self.shapes)


# ---------------------------------------------------------------------------------------------------------------
# Meshes and geography


@dataclass(frozen=True, eq=False)
class Mesh:
    kind = "mesh"
    vertices: np.ndarray        # float64 (n, 3)
    faces: np.ndarray           # int64 (m, 3) triangles
    unit: str | None = None

    def __post_init__(self):
        _array(self.vertices, "vertices", dtypes=(np.dtype(np.float64),), ndim=(2,))
        _array(self.faces, "faces", dtypes=(np.dtype(np.int64),), ndim=(2,))
        if self.vertices.shape[1] != 3 or self.faces.shape[1] != 3:
            raise Malformed("vertices are (n, 3) and faces (m, 3)")
        if self.faces.size and (self.faces.min() < 0 or self.faces.max() >= len(self.vertices)):
            raise Malformed("a face refers to a vertex that does not exist")
        if self.vertices.size and not np.isfinite(self.vertices).all():
            raise Malformed("vertices must be finite")

    def closed(self) -> bool:
        """True when every edge is shared by exactly two triangles (a watertight surface)."""
        if not len(self.faces):
            return False
        edges = np.concatenate([self.faces[:, [0, 1]], self.faces[:, [1, 2]], self.faces[:, [2, 0]]])
        edges.sort(axis=1)
        # one int64 key per undirected edge (vertex counts are far below 2**31), sorted in place: equal keys are
        # one edge's uses. np.unique(axis=0) on the pairs needs several copies of them.
        keys = edges[:, 0] * np.int64(max(1, len(self.vertices))) + edges[:, 1]
        del edges
        keys.sort()
        starts = np.flatnonzero(np.concatenate(([True], keys[1:] != keys[:-1], [True])))
        return bool((np.diff(starts) == 2).all())

    def describe(self) -> dict:
        out = {"kind": self.kind, "n_vertices": len(self.vertices), "n_faces": len(self.faces),
               "unit": _cut(self.unit), "closed": self.closed()}
        if len(self.vertices):
            out["bounds_min"] = [_num(v) for v in self.vertices.min(axis=0)]
            out["bounds_max"] = [_num(v) for v in self.vertices.max(axis=0)]
        return bounded(out)

    def shape(self) -> ContentShape:
        return _shape(("solid",) if self.closed() else ("surface",), n_items=len(self.vertices), has_space=3)

    def text(self) -> str:
        return ""


@dataclass(frozen=True, eq=False)
class Feature:
    geometry: str               # "Point", "LineString", "Polygon", ... (GeoJSON names)
    coordinates: np.ndarray     # float64 (n, 2) or (n, 3)
    properties: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self):
        _array(self.coordinates, "coordinates", dtypes=(np.dtype(np.float64),), ndim=(2,))
        if self.coordinates.shape[1] not in (2, 3):
            raise Malformed("coordinates are (n, 2) or (n, 3)")


@dataclass(frozen=True, eq=False)
class Features:
    kind = "features"
    features: tuple[Feature, ...]
    crs: str | None = None

    def __post_init__(self):
        object.__setattr__(self, "features", tuple(self.features))
        if not all(isinstance(f, Feature) for f in self.features):
            raise Malformed("features must be Feature objects")

    def describe(self) -> dict:
        counts: dict[str, int] = {}
        keys: dict[str, int] = {}
        for f in self.features:
            counts[f.geometry] = counts.get(f.geometry, 0) + 1
            for k in f.properties:
                keys[k] = keys.get(k, 0) + 1
        out = {"kind": self.kind, "n_features": len(self.features), "crs": _cut(self.crs),
               "geometry_counts": dict(list(counts.items())[:LIST_SAMPLE]),
               "property_keys": [_cut(k) for k in list(keys)[:LIST_SAMPLE]]}
        coords = [f.coordinates[:, :2] for f in self.features if len(f.coordinates)]
        if coords:
            allc = np.concatenate(coords)
            out["bounds"] = [_num(allc[:, 0].min()), _num(allc[:, 1].min()), _num(allc[:, 0].max()),
                             _num(allc[:, 1].max())]
        return bounded(out)

    def shape(self) -> ContentShape:
        # timestamps (`times_s`, lane B2's GPX/KML convention) make the places a timeline too
        has_time = any(f.properties.get("times_s") for f in self.features)
        return _shape(("map", "timeline") if has_time else ("map",), n_items=len(self.features), has_time=has_time,
                      has_space=2, text_chars=len(self.text()))

    def text(self) -> str:
        return _bounded_text(v for f in self.features for v in f.properties.values() if isinstance(v, str))


# ---------------------------------------------------------------------------------------------------------------
# Trees and graphs


@dataclass(frozen=True)
class Node:
    name: str
    value: object | None = None
    attrs: Mapping[str, str] = field(default_factory=dict)
    children: tuple[Node, ...] = ()

    def __post_init__(self):
        object.__setattr__(self, "children", tuple(self.children))


def _walk(root: Node, limit: int = 5_000_000):
    """Nodes depth-first with their depth, iteratively (no recursion limit), at most `limit` nodes."""
    stack, seen = [(root, 0)], 0
    while stack and seen < limit:
        node, depth = stack.pop()
        seen += 1
        yield node, depth
        stack.extend((child, depth + 1) for child in reversed(node.children))


@dataclass(frozen=True)
class Tree:
    kind = "tree"
    root: Node

    def __post_init__(self):
        if not isinstance(self.root, Node):
            raise Malformed("a tree's root is a Node")

    def describe(self) -> dict:
        n, depth, names = 0, 0, {}
        for node, d in _walk(self.root):
            n += 1
            depth = max(depth, d)
            if len(names) < LIST_SAMPLE:
                names[node.name] = names.get(node.name, 0) + 1
            elif node.name in names:
                names[node.name] += 1
        return bounded({"kind": self.kind, "root": _cut(self.root.name), "n_nodes": n, "depth": depth,
                        "n_root_children": len(self.root.children), "name_counts": names})

    def shape(self) -> ContentShape:
        n = sum(1 for _ in _walk(self.root))
        return _shape(("hierarchy",), n_items=n, text_chars=len(self.text()))

    def text(self) -> str:
        return _bounded_text(node.value for node, _ in _walk(self.root) if isinstance(node.value, str))


@dataclass(frozen=True)
class GNode:
    id: str
    kind: str
    label: str
    attrs: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class Edge:
    src: str
    dst: str
    kind: str


@dataclass(frozen=True)
class Graph:
    kind = "graph"
    nodes: tuple[GNode, ...]
    edges: tuple[Edge, ...]

    def __post_init__(self):
        object.__setattr__(self, "nodes", tuple(self.nodes))
        object.__setattr__(self, "edges", tuple(self.edges))
        ids = {n.id for n in self.nodes}
        if len(ids) != len(self.nodes):
            raise Malformed("node ids must be unique")
        if any(e.src not in ids or e.dst not in ids for e in self.edges):
            raise Malformed("an edge refers to a node that does not exist")

    def describe(self) -> dict:
        node_kinds: dict[str, int] = {}
        edge_kinds: dict[str, int] = {}
        for n in self.nodes:
            node_kinds[n.kind] = node_kinds.get(n.kind, 0) + 1
        for e in self.edges:
            edge_kinds[e.kind] = edge_kinds.get(e.kind, 0) + 1
        return bounded({"kind": self.kind, "n_nodes": len(self.nodes), "n_edges": len(self.edges),
                        "node_kinds": dict(list(node_kinds.items())[:LIST_SAMPLE]),
                        "edge_kinds": dict(list(edge_kinds.items())[:LIST_SAMPLE])})

    def shape(self) -> ContentShape:
        return _shape(("network",), n_items=len(self.nodes), text_chars=len(self.text()))

    def text(self) -> str:
        return _bounded_text(n.label for n in self.nodes)


# ---------------------------------------------------------------------------------------------------------------
# Documents and bundles


@dataclass(frozen=True)
class Block:
    kind: str                   # BLOCK_KINDS
    level: int
    text: str
    ref: object | None = None   # Table for "table"; caption/alt text (and member name) for "figure"

    def __post_init__(self):
        if self.kind not in BLOCK_KINDS:
            raise Malformed(f"unknown block kind {self.kind!r}")
        if self.kind == "table" and self.ref is not None and not isinstance(self.ref, Table):
            raise Malformed("a table block's ref is a Table")


@dataclass(frozen=True)
class Document:
    kind = "document"
    blocks: tuple[Block, ...]
    title: str | None = None

    def __post_init__(self):
        object.__setattr__(self, "blocks", tuple(self.blocks))
        if not all(isinstance(b, Block) for b in self.blocks):
            raise Malformed("a document holds only Block objects")

    def describe(self) -> dict:
        counts: dict[str, int] = {}
        for b in self.blocks:
            counts[b.kind] = counts.get(b.kind, 0) + 1
        headings = [{"level": b.level, "text": _cut(b.text)} for b in self.blocks if b.kind == "heading"]
        return bounded({"kind": self.kind, "title": _cut(self.title), "n_blocks": len(self.blocks),
                        "block_counts": counts, "headings": headings[:LIST_SAMPLE],
                        "text_chars": sum(len(b.text) for b in self.blocks)})

    def shape(self) -> ContentShape:
        kinds = ["prose"]
        if any(b.kind == "table" for b in self.blocks):
            kinds.append("records")
        if any(b.kind == "code" for b in self.blocks):
            kinds.append("code")
        if any(b.kind == "heading" for b in self.blocks):
            kinds.append("hierarchy")
        return _shape(kinds, n_items=len(self.blocks), text_chars=len(self.text()))

    def text(self) -> str:
        return _bounded_text(([self.title] if self.title else []) + [b.text for b in self.blocks])


@dataclass(frozen=True)
class Member:
    path: str                   # the member's name inside the container, as stored: never followed
    size: int
    detected: Detected
    inner: object | None = None
    note: str = ""


@dataclass(frozen=True)
class Bundle:
    kind = "bundle"
    members: tuple[Member, ...]

    def __post_init__(self):
        object.__setattr__(self, "members", tuple(self.members))
        if not all(isinstance(m, Member) for m in self.members):
            raise Malformed("a bundle holds only Member objects")

    def describe(self) -> dict:
        families: dict[str, int] = {}
        for m in self.members:
            families[m.detected.family] = families.get(m.detected.family, 0) + 1
        return bounded({"kind": self.kind, "n_members": len(self.members),
                        "total_size": sum(m.size for m in self.members), "family_counts": families,
                        "members": [{"path": _cut(m.path), "size": m.size, "format": m.detected.format,
                                     "decoded": m.inner is not None, "note": _cut(m.note)}
                                    for m in self.members[:LIST_SAMPLE]]})

    def shape(self) -> ContentShape:
        return _shape(("hierarchy",), n_items=len(self.members), text_chars=len(self.text()))

    def text(self) -> str:
        return _bounded_text(m.inner.text() for m in self.members if hasattr(m.inner, "text"))


@dataclass(frozen=True)
class Opaque:
    kind = "opaque"
    size: int
    findings: tuple[str, ...]
    candidates: tuple[str, ...]
    needs: tuple[str, ...]

    def __post_init__(self):
        for name in ("findings", "candidates", "needs"):
            object.__setattr__(self, name, _strings(getattr(self, name), name))

    def describe(self) -> dict:
        return bounded({"kind": self.kind, "size": self.size, "findings": list(self.findings[:LIST_SAMPLE]),
                        "candidates": list(self.candidates[:LIST_SAMPLE]), "needs": list(self.needs[:LIST_SAMPLE])})

    def shape(self) -> ContentShape:
        return _shape((), n_items=0)

    def text(self) -> str:
        return ""


REPRESENTATIONS = (Table, Signal, Raster, Frames, Drawing, Mesh, Features, Tree, Graph, Document, Bundle, Opaque)


class HeaderOnly(Unsupported):
    """The format was identified and its header read, but its content is not decoded here (JPEG pixels, MP3
    samples, ...). An addition to DESIGN §2 (ADR-072, integration): `facts` is a JSON-able dict of what the header
    established, `words` the plain-text metadata it carries (untrusted, possibly ""), `opaque` the same facts as an
    `Opaque` with what would be needed. Consumers (the store's index and text layer, `nsf inspect`, `facts`) keep all
    three: a header-only file is never reported as nothing."""

    def __init__(self, message: str, *, facts: dict, needs: tuple[str, ...], size: int, words: str = ""):
        super().__init__(message, needs=needs)
        self.facts = dict(facts)
        self.words = _bounded_text([words]) if words else ""
        self.opaque = Opaque(size, tuple(f"{k}: {v}" for k, v in self.facts.items()), (), tuple(needs))


@dataclass(frozen=True)
class Decoded:
    detected: Detected
    representation: object      # one of REPRESENTATIONS
    notes: tuple[str, ...] = ()  # what was NOT recovered
    partial: bool = False       # True when a limit stopped the decode early; notes say where

    def __post_init__(self):
        object.__setattr__(self, "notes", _strings(self.notes, "notes"))
        if not isinstance(self.representation, REPRESENTATIONS):
            raise Malformed(f"{type(self.representation).__name__} is not a representation")


__all__ = ["ArtifactError", "BASES", "BLOCK_KINDS", "Block", "Bundle", "COLUMN_KINDS", "Column", "ContentShape",
           "DESCRIBE_MAX_BYTES", "Decoded", "Detected", "Document", "Drawing", "ERROR_REASONS", "Edge", "FAMILIES",
           "Feature", "Features", "Frames", "GNode", "Graph", "HeaderOnly", "Limits", "Malformed", "Member", "Mesh", "Node",
           "Opaque", "REPRESENTATIONS", "Raster", "Refused", "SHAPE_KINDS", "Shape", "Signal", "TEXT_MAX_CHARS",
           "Table", "Tree", "Unsupported", "bounded"]
