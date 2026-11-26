"""Geographic features: GeoJSON, GPX, KML, KMZ, WKT -> `Features`.

Each feature has one coordinate array (n, 2) or (n, 3) (a missing elevation is NaN). Structure that one array
cannot show is added to the properties under keys that start with an underscore, set by this decoder:
`_parts` (point counts of each ring of a polygon or each part of a multi-geometry, in order), `_polygons` (rings per
polygon of a MultiPolygon), `_collection` (the index of a member of a GeometryCollection), `_kind` (GPX: waypoint,
route, track). Timestamps, when the file has them, are `times_s` (seconds since the epoch, UTC; NaN where a point
has none). The words of a feature (names, descriptions, string properties) are the artifact's own (untrusted).

CRS: GeoJSON's legacy `crs` member when present, else "OGC:CRS84" (RFC 7946 fixes longitude/latitude on WGS 84);
GPX and KML are WGS 84 by their specifications ("OGC:CRS84"); WKT has none unless an EWKT `SRID=n;` prefix gives
"EPSG:n". KML NetworkLinks and every other reference are recorded and never fetched.

Bounds: coordinates counted against `max_elements`, features against `max_elements`, nesting of GeometryCollections
to 8; the memory Python's JSON and XML parsers need (measured factors x the input size) is checked against
`max_decoded_bytes` before parsing; XML through the safe parser; KMZ through lane K's `SafeZip`. A GeoJSON
coordinate given as true/false is read as 1/0 (numpy's conversion), not refused.
"""
from __future__ import annotations

import json
import math
import re
from datetime import datetime, timezone

import numpy as np

from ..safe import Deadline, SafeZip
from ..safe_b2 import count, local, need, parse_xml, screen_words
from ..types import Decoded, Feature, Features, Malformed, Refused
from . import Adapter

CRS84 = "OGC:CRS84"
MAX_COLLECTION_DEPTH = 8


def parse_time(text: str | None) -> float:
    if not text:
        return math.nan
    try:
        t = datetime.fromisoformat(text.strip().replace("Z", "+00:00"))
    except ValueError:
        return math.nan
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return t.timestamp()


def _array(positions: list) -> np.ndarray:
    if not positions:
        return np.zeros((0, 2))
    dims = 3 if any(len(p) >= 3 for p in positions) else 2
    arr = np.full((len(positions), dims), np.nan)
    for i, p in enumerate(positions):
        arr[i, :min(dims, len(p))] = p[:dims]
    if not np.isfinite(arr[:, :2]).all():
        raise Malformed("a coordinate is not a finite number")
    return arr


class _Budget:
    def __init__(self, limits):
        self.limits, self.n, self.deadline = limits, 0, Deadline(limits.max_seconds)

    def add(self, n):
        self.n += n
        count(self.n, self.limits, "coordinates")
        if self.n % 65536 < n:
            self.deadline.check("reading coordinates")


# ------------------------------------------------------------------------------------------------------------ GeoJSON


def _pos(p) -> list[float]:
    if not isinstance(p, list) or len(p) < 2 or not all(isinstance(v, (int, float)) and not isinstance(v, bool)
                                                         for v in p):
        raise Malformed("a GeoJSON position is not a list of at least two numbers")
    return [float(v) for v in p[:3]]


def _positions(seq) -> np.ndarray:
    """(n, 2|3) float64 from a list of GeoJSON positions: vectorised when the list is regular, else checked one by
    one (which names the first bad position)."""
    try:
        arr = np.asarray(seq, dtype=np.float64)
    except (ValueError, TypeError):
        arr = None
    if arr is None or arr.ndim != 2 or arr.shape[1] < 2:
        if not isinstance(seq, list):
            raise Malformed("GeoJSON coordinates are not a list of positions")
        return _array([_pos(p) for p in seq])
    arr = arr[:, :3]
    if not np.isfinite(arr[:, :2]).all():
        raise Malformed("a coordinate is not a finite number")
    return arr


def _stack(parts: list) -> np.ndarray:
    if not parts:
        return np.zeros((0, 2))
    dims = max(p.shape[1] for p in parts)
    if dims == 3:
        parts = [p if p.shape[1] == 3 else np.column_stack([p, np.full(len(p), np.nan)]) for p in parts]
    return np.concatenate(parts)


def _geometry(g, props: dict, budget: _Budget, out: list, depth: int = 0):
    if not isinstance(g, dict):
        if g is None:
            out.append(Feature("None", np.zeros((0, 2)), props))
            return
        raise Malformed("a GeoJSON geometry is not an object")
    kind = g.get("type")
    coords = g.get("coordinates")
    try:
        if kind == "Point":
            arr, extra = _array([_pos(coords)]), {}
        elif kind in ("MultiPoint", "LineString"):
            arr, extra = _positions(coords), {}
        elif kind in ("MultiLineString", "Polygon"):
            parts = [_positions(part) for part in coords]
            arr, extra = _stack(parts), {"_parts": tuple(len(p) for p in parts)}
        elif kind == "MultiPolygon":
            rings = [[_positions(r) for r in poly] for poly in coords]
            arr = _stack([r for poly in rings for r in poly])
            extra = {"_parts": tuple(len(r) for poly in rings for r in poly),
                     "_polygons": tuple(len(poly) for poly in rings)}
        elif kind == "GeometryCollection":
            if depth >= MAX_COLLECTION_DEPTH:
                raise Refused("GeometryCollections nest too deeply", rule="max_depth")
            for i, member in enumerate(g.get("geometries") or []):
                _geometry(member, {**props, "_collection": i}, budget, out, depth + 1)
            return
        else:
            raise Malformed("a GeoJSON geometry has an unknown type")
    except TypeError:
        raise Malformed(f"the coordinates of a GeoJSON {kind} are not nested as its type requires") from None
    budget.add(len(arr))
    out.append(Feature(kind, arr, {**props, **extra}))


JSON_OBJECT_FACTOR = 3      # measured: json.loads needs 1.4-2.6 bytes per byte of coordinate-heavy GeoJSON


def decode_geojson(data: bytes, limits) -> tuple[list[Feature], str, list[str]]:
    need(len(data) * JSON_OBJECT_FACTOR, limits, "the GeoJSON parsed into objects")
    try:
        doc = json.loads(data.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError):
        raise Malformed("the GeoJSON is not valid UTF-8 JSON") from None
    if not isinstance(doc, dict):
        raise Malformed("the GeoJSON root is not an object")
    budget, out, notes = _Budget(limits), [], []
    crs = None
    if isinstance(doc.get("crs"), dict):
        crs = str((doc["crs"].get("properties") or {}).get("name") or doc["crs"].get("type"))[:200]
        notes.append("a legacy 'crs' member names the coordinate system")
    kind = doc.get("type")
    if kind == "FeatureCollection":
        feats = doc.get("features")
        if not isinstance(feats, list):
            raise Malformed("a FeatureCollection has no features list")
        count(len(feats), limits, "features")
        items = feats
    elif kind == "Feature":
        items = [doc]
    else:
        items = [{"type": "Feature", "geometry": doc, "properties": {}}]
    for f in items:
        if not isinstance(f, dict) or f.get("type") != "Feature":
            raise Malformed("a member of the FeatureCollection is not a Feature")
        props = f.get("properties") if isinstance(f.get("properties"), dict) else {}
        if "id" in f:
            props = {**props, "_id": f["id"]}
        _geometry(f.get("geometry"), dict(props), budget, out)
    if crs is None:
        crs = CRS84
        notes.append("no crs member: RFC 7946 coordinates are longitude, latitude on WGS 84 (OGC:CRS84)")
    return out, crs, notes


# ---------------------------------------------------------------------------------------------------------------- GPX


def _child(el, name):
    for c in el:
        if local(c.tag) == name:
            return c
    return None


def _text(el, name):
    c = _child(el, name)
    return c.text.strip() if c is not None and c.text and c.text.strip() else None


def _gpx_points(parent, name, budget):
    pts, times = [], []
    for p in parent:
        if local(p.tag) != name:
            continue
        try:
            lat, lon = float(p.get("lat")), float(p.get("lon"))
        except (TypeError, ValueError):
            raise Malformed(f"a GPX {name} has no numeric lat and lon") from None
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise Malformed(f"a GPX {name} lies outside latitude/longitude ranges")
        ele = _text(p, "ele")
        try:
            pts.append([lon, lat] + ([float(ele)] if ele is not None else []))
        except ValueError:
            pts.append([lon, lat])
        times.append(parse_time(_text(p, "time")))
    budget.add(len(pts))
    return pts, times


def _props(el, kind):
    props = {"_kind": kind}
    for key in ("name", "desc", "cmt", "type", "sym"):
        v = _text(el, key)
        if v:
            props[key] = v
    return props


def decode_gpx(data: bytes, limits):
    root = parse_xml(data, limits, "the GPX document")
    if local(root.tag) != "gpx":
        raise Malformed("the root element is not gpx")
    budget, out = _Budget(limits), []

    def emit(geometry, pts, times, props, parts=None):
        if any(not math.isnan(t) for t in times):
            props["times_s"] = tuple(times)
        if parts:
            props["_parts"] = parts
        out.append(Feature(geometry, _array(pts), props))
    pts, times = _gpx_points(root, "wpt", budget)
    for el, p, t in zip([e for e in root if local(e.tag) == "wpt"], pts, times):
        emit("Point", [p], [t], _props(el, "waypoint"))
    for rte in (e for e in root if local(e.tag) == "rte"):
        p, t = _gpx_points(rte, "rtept", budget)
        emit("LineString", p, t, _props(rte, "route"))
    for trk in (e for e in root if local(e.tag) == "trk"):
        allp, allt, parts = [], [], []
        for seg in (e for e in trk if local(e.tag) == "trkseg"):
            p, t = _gpx_points(seg, "trkpt", budget)
            allp += p
            allt += t
            parts.append(len(p))
        emit("MultiLineString" if len(parts) > 1 else "LineString", allp, allt, _props(trk, "track"),
             tuple(parts) if len(parts) > 1 else None)
    return out, CRS84, ["GPX coordinates are longitude, latitude on WGS 84 (OGC:CRS84)"]


# ---------------------------------------------------------------------------------------------------------------- KML


def _kml_coords(text, budget):
    pts = []
    for tok in (text or "").split():
        parts = tok.split(",")
        try:
            pts.append([float(v) for v in parts[:3]])
        except ValueError:
            raise Malformed("a KML coordinate is not a number") from None
        if len(pts[-1]) < 2:
            raise Malformed("a KML coordinate has fewer than two numbers")
    budget.add(len(pts))
    return pts


def _kml_geometries(el, budget, depth=0):
    """[(geometry type, points, extra props)] of a KML geometry element (MultiGeometry flattened)."""
    tag = local(el.tag)
    if tag == "Point":
        return [("Point", _kml_coords(_text(el, "coordinates"), budget), {})]
    if tag in ("LineString", "LinearRing"):
        return [("LineString", _kml_coords(_text(el, "coordinates"), budget), {})]
    if tag == "Polygon":
        rings = []
        for b in el:
            if local(b.tag) in ("outerBoundaryIs", "innerBoundaryIs"):
                ring = _child(b, "LinearRing")
                if ring is not None:
                    rings.append(_kml_coords(_text(ring, "coordinates"), budget))
        return [("Polygon", [p for r in rings for p in r], {"_parts": tuple(len(r) for r in rings)})]
    if tag == "MultiGeometry" and depth < MAX_COLLECTION_DEPTH:
        out = []
        for i, c in enumerate(el):
            for g, p, x in _kml_geometries(c, budget, depth + 1):
                out.append((g, p, {**x, "_collection": i}))
        return out
    if tag == "Track":
        whens = [parse_time(c.text) for c in el if local(c.tag) == "when"]
        coords = []
        for c in el:
            if local(c.tag) == "coord":
                try:
                    coords.append([float(v) for v in (c.text or "").split()[:3]])
                except ValueError:
                    raise Malformed("a KML gx:coord is not numeric") from None
        budget.add(len(coords))
        extra = {"times_s": tuple(whens)} if len(whens) == len(coords) and whens else {}
        return [("LineString", coords, extra)]
    return []


def decode_kml(data: bytes, limits):
    root = parse_xml(data, limits, "the KML document")
    if local(root.tag) != "kml":
        raise Malformed("the root element is not kml")
    budget, out, notes = _Budget(limits), [], ["KML coordinates are longitude, latitude on WGS 84 (OGC:CRS84)"]
    links = sum(1 for el in root.iter() if local(el.tag) in ("NetworkLink", "href"))
    for pm in (el for el in root.iter() if local(el.tag) == "Placemark"):
        props = {}
        for key in ("name", "description", "address"):
            v = _text(pm, key)
            if v:
                props[key] = v
        ts = _child(pm, "TimeStamp")
        span = _child(pm, "TimeSpan")
        when = parse_time(_text(ts, "when")) if ts is not None else math.nan
        ext = _child(pm, "ExtendedData")
        if ext is not None:
            for d in ext.iter():
                if local(d.tag) == "Data" and d.get("name"):
                    props[d.get("name")[:120]] = _text(d, "value") or ""
        if span is not None:
            props["time_begin"] = _text(span, "begin") or ""
            props["time_end"] = _text(span, "end") or ""
        for c in pm:
            for geometry, pts, extra in _kml_geometries(c, budget):
                p = {**props, **extra}
                if not math.isnan(when) and "times_s" not in p:
                    p["times_s"] = (when,) * max(1, len(pts))
                out.append(Feature(geometry, _array(pts), p))
    count(len(out), limits, "features")
    if links:
        notes.append(f"{links} link(s) to other documents or images recorded; never fetched")
    return out, CRS84, notes


def decode_kmz(data: bytes, limits):
    archive = SafeZip(data, limits)
    kml = [e for e in archive.entries if e.name.lower().endswith(".kml") and not e.is_dir]
    if not kml:
        raise Malformed("the KMZ archive holds no .kml document")
    main = next((e for e in kml if e.name.lower() == "doc.kml"), kml[0])
    features, crs, notes = decode_kml(archive.read(main.name), limits)
    others = [e.name for e in archive.entries if e is not main]
    if others:
        notes.append(f"{len(others)} other member(s) of the KMZ (images, further documents) not read")
    return features, crs, notes


# ---------------------------------------------------------------------------------------------------------------- WKT

_WKT_TOKEN = re.compile(r"\s*(\(|\)|,|[A-Za-z]+|[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)")


WKT_NAMES = {"POINT": "Point", "MULTIPOINT": "MultiPoint", "LINESTRING": "LineString", "POLYGON": "Polygon",
             "MULTILINESTRING": "MultiLineString", "MULTIPOLYGON": "MultiPolygon", "TRIANGLE": "Polygon",
             "TIN": "MultiPolygon", "POLYHEDRALSURFACE": "MultiPolygon", "GEOMETRYCOLLECTION": "GeometryCollection"}


def split_wkt(text: str) -> list[str]:
    """Pieces of a WKT text separated by newlines or semicolons outside parentheses."""
    out, depth, cur = [], 0, []
    for ch in text:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch in "\n;" and depth <= 0:
            out.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    out.append("".join(cur))
    return out


class _WKTParser:
    def __init__(self, text, budget):
        self.toks = [t for t in _WKT_TOKEN.findall(text)]
        self.i, self.budget, self.dropped_m = 0, budget, False

    def peek(self):
        return self.toks[self.i].upper() if self.i < len(self.toks) else None

    def take(self, want=None):
        tok = self.peek()
        if tok is None or (want is not None and tok != want):
            raise Malformed(f"the WKT text expects {want or 'more'} at token {self.i}")
        self.i += 1
        return tok

    def position(self, has_m):
        vals = []
        while self.peek() not in (",", ")", None):
            try:
                vals.append(float(self.take()))
            except ValueError:
                raise Malformed("a WKT coordinate is not a number") from None
        if has_m and len(vals) >= 3:
            vals = vals[:-1]
            self.dropped_m = True
        if len(vals) < 2:
            raise Malformed("a WKT position has fewer than two numbers")
        return vals[:3]

    def seq(self, item):
        self.take("(")
        out = [item()]
        while self.peek() == ",":
            self.take(",")
            out.append(item())
        self.take(")")
        return out

    def geometry(self, depth=0):
        kind = self.take()
        dim = self.peek()
        has_m = False
        if dim in ("Z", "M", "ZM"):
            self.take()
            has_m = "M" in dim
        if self.peek() == "EMPTY":
            self.take()
            return [(WKT_NAMES.get(kind, kind.title()), [], {})]

        def pos():
            return self.position(has_m)

        def point_in_multi():
            if self.peek() == "(":
                return self.seq(pos)[0]
            return pos()
        if kind == "POINT":
            pts = self.seq(pos)
            return [("Point", pts, {})]
        if kind == "MULTIPOINT":
            return [("MultiPoint", self.seq(point_in_multi), {})]
        if kind == "LINESTRING":
            return [("LineString", self.seq(pos), {})]
        if kind in ("POLYGON", "MULTILINESTRING", "TRIANGLE"):
            rings = self.seq(lambda: self.seq(pos))
            name = {"POLYGON": "Polygon", "TRIANGLE": "Polygon", "MULTILINESTRING": "MultiLineString"}[kind]
            return [(name, [p for r in rings for p in r], {"_parts": tuple(len(r) for r in rings)})]
        if kind in ("MULTIPOLYGON", "POLYHEDRALSURFACE", "TIN"):
            polys = self.seq(lambda: self.seq(lambda: self.seq(pos)))
            return [("MultiPolygon", [p for poly in polys for r in poly for p in r],
                     {"_parts": tuple(len(r) for poly in polys for r in poly),
                      "_polygons": tuple(len(poly) for poly in polys)})]
        if kind == "GEOMETRYCOLLECTION":
            if depth >= MAX_COLLECTION_DEPTH:
                raise Refused("GeometryCollections nest too deeply", rule="max_depth")
            members = self.seq(lambda: self.geometry(depth + 1))
            return [(g, p, {**x, "_collection": i}) for i, m in enumerate(members) for g, p, x in m]
        raise Malformed("the WKT text names an unknown geometry")


WKT_OBJECT_FACTOR = 16      # measured 15.4: tokens and positions per byte of coordinate-dense WKT


def decode_wkt(data: bytes, limits):
    need(len(data) * WKT_OBJECT_FACTOR, limits, "the WKT text parsed into tokens")
    text = data.decode("utf-8", "replace")
    budget, out, notes, crs = _Budget(limits), [], [], None
    for line in split_wkt(text):
        line = line.strip()
        if not line:
            continue
        m = re.match(r"SRID=(\d+)$", line, re.I)
        if m:
            crs = f"EPSG:{m.group(1)}"
            continue
        parser = _WKTParser(line, budget)
        for kind, pts, extra in parser.geometry():
            budget.add(len(pts))
            out.append(Feature(kind, _array(pts), extra))
        if parser.peek() is not None:
            raise Malformed("the WKT text continues after a complete geometry")
        if parser.dropped_m:
            notes.append("measure (M) values are not kept")
    if not out:
        raise Malformed("the WKT text holds no geometry")
    count(len(out), limits, "features")
    if crs is None:
        notes.append("WKT carries no coordinate system")
    return out, crs, notes


DECODERS = {"geojson": decode_geojson, "gpx": decode_gpx, "kml": decode_kml, "kmz": decode_kmz, "wkt": decode_wkt}


class GeoAdapter(Adapter):
    name = "b2-geo"
    formats = tuple(DECODERS)
    families = ("geo",)

    def decode(self, data, detected, limits):
        features, crs, notes = DECODERS[detected.format](data, limits)
        rep = Features(tuple(features), crs)
        notes = list(notes) + list(screen_words(rep.text()))
        return Decoded(detected, rep, tuple(notes), False)


ADAPTERS = (GeoAdapter(),)

__all__ = ["ADAPTERS", "CRS84", "GeoAdapter", "decode_geojson", "decode_gpx", "decode_kml", "decode_kmz",
           "decode_wkt", "parse_time"]
