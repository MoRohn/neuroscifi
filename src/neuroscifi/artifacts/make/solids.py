"""Solids and surfaces from a specification (DESIGN §6, ADR-074 lane G2): binary STL or Wavefront OBJ.

Forms (each a reading contract the checker re-derives from the spec alone):
- `solid_box`: a box of `size` [x, y, z] centred on `center`.
- `solid_extrude`: a 2-D outline with optional holes, extruded along z from `base_z` by `height`.
- `solid_revolve`: a profile of [r, z] points turned about the z axis; ring vertices at the angles 2*pi*j/n
  (j = 0 .. n-1) from the +x axis; an end of the profile off the axis is closed by a flat cap.
- `surface_grid`: z = values[i][j] at x = x0 + j*(x1-x0)/(nx-1), y = y0 + i*(y1-y0)/(ny-1); each cell is split along
  the diagonal from its (x low, y low) corner to its (x high, y high) corner; with `base_z` it is closed into a solid
  (walls down to the base and a flat bottom).
- `solid_points`: a small octahedron of half-size `marker` at each point (default: 1/100 of the points' largest
  extent, or 1 when they all coincide).

Every solid is watertight with outward normals (the faces are flipped together if the signed volume came out
negative); nothing is "fixed" silently: an outline that crosses itself, a hole outside its outline, a base above the
surface or a profile that crosses the axis is refused with a named reason. Vertex order is deterministic, and the
angles use the host-independent sine of make/sound.py, so the same spec gives the same bytes everywhere.
Nothing made here is evidence (COMMON, ADR-049, ADR-071).
"""
from __future__ import annotations

import math
import struct

import numpy as np

from ..spec import Spec
from ..types import Limits, Malformed, Refused
from .sound import sin_turns

UNITS = ["mm", "cm", "m", "km", "um", "in", "ft", "unitless"]
MAX_RING_POINTS = 1000
MAX_HOLES = 32
MAX_TRIANGLES = 2_000_000
_NUM = {"type": "number", "minimum": -1e9, "maximum": 1e9}
_POINT2 = {"type": "array", "minItems": 2, "maxItems": 2, "items": _NUM}
_POINT3 = {"type": "array", "minItems": 3, "maxItems": 3, "items": _NUM}
_RING = {"type": "array", "minItems": 3, "maxItems": MAX_RING_POINTS, "items": _POINT2}
_OPTIONS = {"type": "object", "additionalProperties": False, "properties": {
    "format": {"type": "string", "enum": ["stl", "obj"]},
    "unit": {"type": "string", "enum": UNITS},
    "history": {"type": "array", "maxItems": 3, "items": {"type": "string", "maxLength": 300}}}}


def _data(required, properties):
    return {"type": "object", "additionalProperties": False, "required": required, "properties": properties}


def _options(extra=None):
    return {**_OPTIONS, "properties": {**_OPTIONS["properties"], **(extra or {})}}


SCHEMAS = {
    "solid_box": {"data": _data(["size"], {"size": {**_POINT3, "items": {"type": "number", "minimum": 1e-6,
                                                                          "maximum": 1e9}},
                                           "center": _POINT3}),
                  "options": _options()},
    "solid_extrude": {"data": _data(["outline", "height"], {
        "outline": _RING, "holes": {"type": "array", "maxItems": MAX_HOLES, "items": _RING},
        "height": {"type": "number", "minimum": 1e-6, "maximum": 1e9}, "base_z": _NUM}), "options": _options()},
    "solid_revolve": {"data": _data(["profile"], {
        "profile": {"type": "array", "minItems": 2, "maxItems": MAX_RING_POINTS, "items": _POINT2}}),
        "options": _options({"segments": {"type": "integer", "minimum": 8, "maximum": 512}})},
    "surface_grid": {"data": _data(["values", "x", "y"], {
        "values": {"type": "array", "minItems": 2, "maxItems": 512,
                   "items": {"type": "array", "minItems": 2, "maxItems": 512, "items": _NUM}},
        "x": _POINT2, "y": _POINT2, "base_z": _NUM}), "options": _options()},
    "solid_points": {"data": _data(["points"], {
        "points": {"type": "array", "minItems": 1, "maxItems": 20000, "items": _POINT3}}),
        "options": _options({"marker": {"type": "number", "minimum": 1e-9, "maximum": 1e9}})},
}


# ---------------------------------------------------------------------------------------------------------------
# Plane geometry


def _area(ring: np.ndarray) -> float:
    x, y = ring[:, 0], ring[:, 1]
    return 0.5 * float(np.dot(x, np.roll(y, -1)) - np.dot(np.roll(x, -1), y))


def _segments_cross(edges: np.ndarray) -> bool:
    """True if any two edges that are not neighbours in the same ring touch or cross. `edges` is (n, 5):
    x1, y1, x2, y2, ring id; edges of one ring are consecutive."""
    p, q = edges[:, 0:2], edges[:, 2:4]
    ring = edges[:, 4]
    n = len(edges)
    first = {}
    count = {}
    for i, r in enumerate(ring):
        first.setdefault(r, i)
        count[r] = count.get(r, 0) + 1

    def orient(a, b, c):
        return (b[..., 0] - a[..., 0]) * (c[..., 1] - a[..., 1]) - (b[..., 1] - a[..., 1]) * (c[..., 0] - a[..., 0])
    for i in range(n - 1):
        j = np.arange(i + 1, n)
        same = ring[j] == ring[i]
        r0, rc = first[ring[i]], count[ring[i]]
        adjacent = same & (((j - r0) == ((i - r0 + 1) % rc)) | (((i - r0) == ((j - r0 + 1) % rc))))
        a, b, c, d = p[i], q[i], p[j], q[j]
        o1, o2 = orient(a, b, c), orient(a, b, d)
        o3, o4 = orient(c, d, a), orient(c, d, b)
        proper = (o1 * o2 < 0) & (o3 * o4 < 0)

        def on(s0, s1, pt, o):
            return (o == 0) & (np.minimum(s0[..., 0], s1[..., 0]) <= pt[..., 0]) & (pt[..., 0] <= np.maximum(
                s0[..., 0], s1[..., 0])) & (np.minimum(s0[..., 1], s1[..., 1]) <= pt[..., 1]) & (
                pt[..., 1] <= np.maximum(s0[..., 1], s1[..., 1]))
        touch = on(a, b, c, o1) | on(a, b, d, o2) | on(c, d, a, o3) | on(c, d, b, o4)
        if np.any((proper | touch) & ~adjacent):
            return True
    return False


def _inside(pt, ring: np.ndarray) -> bool:
    x, y = pt
    xi, yi = ring[:, 0], ring[:, 1]
    xj, yj = np.roll(xi, 1), np.roll(yi, 1)
    crosses = (yi > y) != (yj > y)
    with np.errstate(divide="ignore", invalid="ignore"):
        xs = (xj - xi) * (y - yi) / (yj - yi) + xi
    return bool(np.count_nonzero(crosses & (x < xs)) % 2)


def _check_rings(outer: np.ndarray, holes: list[np.ndarray]) -> None:
    rings = [outer, *holes]
    for r in rings:
        if np.any(np.all(r == np.roll(r, -1, axis=0), axis=1)):
            raise Malformed("an outline repeats a point")
        if _area(r) == 0:
            raise Malformed("an outline encloses no area")
    edges = np.concatenate([np.column_stack([r, np.roll(r, -1, axis=0), np.full(len(r), k)])
                            for k, r in enumerate(rings)])
    if _segments_cross(edges):
        raise Malformed("an outline crosses or touches itself or another outline")
    for k, h in enumerate(holes):
        if not _inside(h[0], outer):
            raise Malformed(f"hole {k + 1} lies outside the outline")
        for m, other in enumerate(holes):
            if m != k and _inside(h[0], other):
                raise Malformed(f"hole {k + 1} lies inside hole {m + 1}")


def _bridge(points: np.ndarray, outer: list[int], holes: list[list[int]]) -> list[int]:
    """Join each hole to the outline by a two-way cut (Eberly's method), giving one polygon to clip ears from."""
    poly = list(outer)
    for hole in sorted(holes, key=lambda h: (-max(points[i, 0] for i in h), min(h))):
        m_at = max(range(len(hole)), key=lambda k: (points[hole[k], 0], -points[hole[k], 1], -hole[k]))
        m = hole[m_at]
        mx, my = points[m]
        best = None
        for k in range(len(poly)):
            a, b = poly[k], poly[(k + 1) % len(poly)]
            (ax, ay), (bx, by) = points[a], points[b]
            if (ay > my) == (by > my):
                continue
            xi = ax + (my - ay) * (bx - ax) / (by - ay)
            if xi >= mx and (best is None or xi < best[0]):
                best = (xi, k)
        if best is None:
            raise Malformed("a hole could not be joined to its outline")
        xi, k = best
        a, b = poly[k], poly[(k + 1) % len(poly)]
        if points[a, 1] == my and points[a, 0] == xi:
            target = k
        elif points[b, 1] == my and points[b, 0] == xi:
            target = (k + 1) % len(poly)
        else:
            target = k if points[a, 0] > points[b, 0] else (k + 1) % len(poly)
            px, py = points[poly[target]]
            tri = np.array([[mx, my], [xi, my], [px, py]])
            best_r = None
            for t, v in enumerate(poly):
                if t == target or poly[t] == poly[target]:
                    continue
                prev, nxt = points[poly[t - 1]], points[poly[(t + 1) % len(poly)]]
                vx, vy = points[v]
                reflex = (vx - prev[0]) * (nxt[1] - vy) - (vy - prev[1]) * (nxt[0] - vx) <= 0
                if reflex and _in_triangle(np.array([[vx, vy]]), tri)[0]:
                    d = math.hypot(vx - mx, vy - my)
                    key = (-(vx - mx) / d if d else 0.0, d, v)
                    if best_r is None or key < best_r[0]:
                        best_r = (key, t)
            if best_r is not None:
                target = best_r[1]
        loop = hole[m_at:] + hole[:m_at]
        poly = poly[:target + 1] + loop + [m, poly[target]] + poly[target + 1:]
    return poly


def _in_triangle(pts: np.ndarray, tri: np.ndarray) -> np.ndarray:
    """Points inside the triangle or on its edges (the triangle's corners in any order)."""
    a, b, c = tri

    def side(p, q):
        return (q[0] - p[0]) * (pts[:, 1] - p[1]) - (q[1] - p[1]) * (pts[:, 0] - p[0])
    s1, s2, s3 = side(a, b), side(b, c), side(c, a)
    return ((s1 >= 0) & (s2 >= 0) & (s3 >= 0)) | ((s1 <= 0) & (s2 <= 0) & (s3 <= 0))


def triangulate(points: np.ndarray, outer: list[int], holes: list[list[int]]) -> list[tuple[int, int, int]]:
    """Ear clipping of an outline (counter-clockwise) with holes (clockwise). Ears next to a straight angle are clipped
    first, then the ear with the widest smallest angle, so a point on a straight edge never ends in a flat triangle.
    The result is proved: every triangle has area, and their areas sum to the outline's area less the holes'."""
    poly = _bridge(points, outer, holes)
    ids = np.array(poly)
    coords = points[ids]
    n = len(poly)
    prev, nxt = list(np.roll(np.arange(n), 1)), list(np.roll(np.arange(n), -1))
    alive = np.ones(n, dtype=bool)
    tris: list[tuple[int, int, int]] = []

    def cross(a, b, c):
        (ax, ay), (bx, by), (cx, cy) = points[a], points[b], points[c]
        return (bx - ax) * (cy - by) - (by - ay) * (cx - bx)

    def min_angle(a, b, c):
        out = math.pi
        for p, q, r in ((a, b, c), (b, c, a), (c, a, b)):
            u, v = points[q] - points[p], points[r] - points[p]
            nu, nv = math.hypot(*u), math.hypot(*v)
            if nu == 0 or nv == 0:
                return 0.0
            out = min(out, math.acos(max(-1.0, min(1.0, float(np.dot(u, v)) / (nu * nv)))))
        return out

    scale = float(np.ptp(points, axis=0).max()) or 1.0
    eps = 1e-12 * scale * scale

    def straight(k):
        return abs(cross(ids[prev[k]], ids[k], ids[nxt[k]])) <= eps

    def ear(k):
        """The ear's rank (clip the highest first), or None when the corner is not an ear now."""
        a, b, c = ids[prev[k]], ids[k], ids[nxt[k]]
        if cross(a, b, c) <= eps:
            return None
        others = alive & (ids != a) & (ids != b) & (ids != c)
        if np.any(_in_triangle(coords[others], points[[a, b, c]])):
            return None
        return (straight(prev[k]) or straight(nxt[k]), min_angle(a, b, c), -k)

    # Removing a corner never puts a point into another ear's triangle, so a cached ear stays an ear until its own
    # neighbours change; a cached "not an ear" may go stale, so every ear is recomputed every 16 cuts and whenever
    # none is cached.
    ears: dict[int, tuple] = {}
    left = n
    step = 0
    while left > 3:
        if not ears or step % 16 == 0:
            ears = {k: key for k in np.flatnonzero(alive).tolist() if (key := ear(k)) is not None}
            if not ears:
                raise Malformed("the outline could not be cut into triangles")
        k = max(ears, key=ears.__getitem__)
        p, q = prev[k], nxt[k]
        tris.append((int(ids[p]), int(ids[k]), int(ids[q])))
        alive[k] = False
        nxt[p], prev[q] = q, p
        left -= 1
        step += 1
        ears.pop(k)
        for m in {prev[p], p, q, nxt[q]}:
            if alive[m]:
                key = ear(m) if left > 3 else None
                if key is None:
                    ears.pop(m, None)
                else:
                    ears[m] = key
    k = int(np.flatnonzero(alive)[0])
    last = (int(ids[k]), int(ids[nxt[k]]), int(ids[nxt[nxt[k]]]))
    if cross(*last) <= eps:
        raise Malformed("the outline could not be cut into triangles without a flat one")
    tris.append(last)
    total = sum(0.5 * cross(*t) for t in tris)
    expected = _area(points[outer]) - sum(-_area(points[h]) for h in holes)
    if abs(total - expected) > 1e-9 * max(1.0, abs(expected)):
        raise Malformed("the outline's triangles do not cover it exactly")
    return tris


# ---------------------------------------------------------------------------------------------------------------
# Meshes


def _oriented(vertices: np.ndarray, faces: np.ndarray) -> np.ndarray:
    """Flip every face if the closed surface's signed volume is negative (outward normals)."""
    v0, v1, v2 = vertices[faces[:, 0]], vertices[faces[:, 1]], vertices[faces[:, 2]]
    volume = float(np.einsum("ij,ij->i", v0, np.cross(v1, v2)).sum()) / 6.0
    return faces[:, ::-1].copy() if volume < 0 else faces


def box(size, center=(0.0, 0.0, 0.0)):
    s = np.asarray(size, dtype=np.float64) / 2.0
    c = np.asarray(center, dtype=np.float64)
    corners = np.array([[x, y, z] for z in (-1, 1) for y in (-1, 1) for x in (-1, 1)], dtype=np.float64)
    vertices = c + corners * s
    faces = np.array([[0, 2, 1], [1, 2, 3], [4, 5, 6], [5, 7, 6], [0, 1, 4], [1, 5, 4],
                      [2, 6, 3], [3, 6, 7], [0, 4, 2], [2, 4, 6], [1, 3, 5], [3, 7, 5]], dtype=np.int64)
    return vertices, _oriented(vertices, faces)


def extrude(outline, holes, height: float, base_z: float = 0.0):
    outer = np.asarray(outline, dtype=np.float64)
    rings = [np.asarray(h, dtype=np.float64) for h in holes]
    if len(outer) + sum(len(h) for h in rings) > MAX_RING_POINTS:
        raise Refused(f"more than {MAX_RING_POINTS} outline points in all", rule="max_points", reason="too_large")
    _check_rings(outer, rings)
    if _area(outer) < 0:
        outer = outer[::-1]
    rings = [h if _area(h) < 0 else h[::-1] for h in rings]
    plane = np.concatenate([outer, *rings]) if rings else outer
    n = len(plane)
    starts = np.cumsum([0, len(outer)] + [len(h) for h in rings])
    loops = [list(range(starts[k], starts[k + 1])) for k in range(len(rings) + 1)]
    tris = triangulate(plane, loops[0], loops[1:])
    vertices = np.concatenate([np.column_stack([plane, np.full(n, base_z)]),
                               np.column_stack([plane, np.full(n, base_z + height)])])
    faces = [(c, b, a) for a, b, c in tris] + [(a + n, b + n, c + n) for a, b, c in tris]
    for loop in loops:
        for k, a in enumerate(loop):
            b = loop[(k + 1) % len(loop)]
            faces += [(a, b, b + n), (a, b + n, a + n)]
    return vertices, _oriented(vertices, np.array(faces, dtype=np.int64))


def revolve(profile, segments: int = 64):
    prof = np.asarray(profile, dtype=np.float64)
    if np.any(prof[:, 0] < 0):
        raise Malformed("a profile point has a negative radius")
    if np.any(prof[1:-1, 0] == 0):
        raise Malformed("only the ends of a profile may lie on the axis")
    ring = prof.copy()           # the profile closed by the axis: its ends' caps become poles
    if prof[0, 0] > 0:
        ring = np.concatenate([[[0.0, prof[0, 1]]], ring])
    if prof[-1, 0] > 0:
        ring = np.concatenate([ring, [[0.0, prof[-1, 1]]]])
    _check_rings(ring, [])
    turns = np.arange(segments, dtype=np.float64) / segments
    cos, sin = sin_turns(turns + 0.25), sin_turns(turns)
    vertices: list[np.ndarray] = []
    index: list[object] = []
    for r, z in ring:
        if r == 0:
            index.append(sum(len(v) for v in vertices))
            vertices.append(np.array([[0.0, 0.0, z]]))
        else:
            start = sum(len(v) for v in vertices)
            index.append(np.arange(start, start + segments))
            vertices.append(np.column_stack([r * cos, r * sin, np.full(segments, z)]))
    faces = []
    for a, b in zip(index, index[1:], strict=False):
        for j in range(segments):
            k = (j + 1) % segments
            if isinstance(a, int):
                faces.append((a, b[j], b[k]))
            elif isinstance(b, int):
                faces.append((a[j], b, a[k]))
            else:
                faces += [(a[j], b[j], b[k]), (a[j], b[k], a[k])]
    verts = np.concatenate(vertices)
    return verts, _oriented(verts, np.array(faces, dtype=np.int64))


def surface(values, x, y, base_z=None):
    if len({len(row) for row in values}) != 1:
        raise Malformed("every row of the grid must have the same length")
    z = np.asarray(values, dtype=np.float64)
    ny, nx = z.shape
    if x[1] <= x[0] or y[1] <= y[0]:
        raise Malformed("each axis range must rise")
    xs = x[0] + np.arange(nx) * ((x[1] - x[0]) / (nx - 1))
    ys = y[0] + np.arange(ny) * ((y[1] - y[0]) / (ny - 1))
    gx, gy = np.meshgrid(xs, ys)
    top = np.column_stack([gx.ravel(), gy.ravel(), z.ravel()])
    idx = np.arange(nx * ny).reshape(ny, nx)
    a, b, c, d = idx[:-1, :-1].ravel(), idx[:-1, 1:].ravel(), idx[1:, 1:].ravel(), idx[1:, :-1].ravel()
    faces = np.concatenate([np.column_stack([a, b, c]), np.column_stack([a, c, d])])
    if base_z is None:
        return top, faces.astype(np.int64)
    if base_z >= float(z.min()):
        raise Malformed("the base must lie below the lowest value of the surface")
    rim = list(idx[0, :]) + list(idx[1:, -1]) + list(idx[-1, -2::-1]) + list(idx[-2:0:-1, 0])
    m = len(top)
    bottom = np.column_stack([top[rim, 0], top[rim, 1], np.full(len(rim), float(base_z))])
    centre = np.array([[(x[0] + x[1]) / 2.0, (y[0] + y[1]) / 2.0, float(base_z)]])
    vertices = np.concatenate([top, bottom, centre])
    extra = []
    for k, t0 in enumerate(rim):
        t1 = rim[(k + 1) % len(rim)]
        b0, b1 = m + k, m + (k + 1) % len(rim)
        extra += [(t0, b0, b1), (t0, b1, t1), (b1, b0, len(vertices) - 1)]
    faces = np.concatenate([faces, np.array(extra)]).astype(np.int64)
    return vertices, _oriented(vertices, faces)


def default_marker(points) -> float:
    p = np.asarray(points, dtype=np.float64)
    extent = float(np.ptp(p, axis=0).max()) if len(p) > 1 else 0.0
    return extent / 100.0 if extent > 0 else 1.0


def markers(points, marker: float | None = None):
    p = np.asarray(points, dtype=np.float64)
    s = default_marker(points) if marker is None else float(marker)
    offsets = np.array([[s, 0, 0], [-s, 0, 0], [0, s, 0], [0, -s, 0], [0, 0, s], [0, 0, -s]], dtype=np.float64)
    octa = np.array([[0, 2, 4], [2, 1, 4], [1, 3, 4], [3, 0, 4], [2, 0, 5], [1, 2, 5], [3, 1, 5], [0, 3, 5]])
    vertices = (p[:, None, :] + offsets[None, :, :]).reshape(-1, 3)
    faces = (octa[None, :, :] + 6 * np.arange(len(p))[:, None, None]).reshape(-1, 3)
    return vertices, faces.astype(np.int64)


def mesh_of(spec: Spec) -> tuple[np.ndarray, np.ndarray]:
    d = spec.data
    form = spec.spec
    if form == "solid_box":
        return box(d["size"], d.get("center", (0.0, 0.0, 0.0)))
    if form == "solid_extrude":
        return extrude(d["outline"], d.get("holes", []), float(d["height"]), float(d.get("base_z", 0.0)))
    if form == "solid_revolve":
        return revolve(d["profile"], int(spec.options.get("segments", 64)))
    if form == "surface_grid":
        return surface(d["values"], d["x"], d["y"], d.get("base_z"))
    if form == "solid_points":
        return markers(d["points"], spec.options.get("marker"))
    raise Malformed(f"{form} is not a solid form")


# ---------------------------------------------------------------------------------------------------------------
# Bytes


def stl_bytes(vertices: np.ndarray, faces: np.ndarray, header: str) -> bytes:
    tri = vertices[faces]
    normal = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    length = np.linalg.norm(normal, axis=1, keepdims=True)
    normal = np.divide(normal, length, out=np.zeros_like(normal), where=length > 0)
    record = np.zeros(len(faces), dtype=np.dtype([("n", "<f4", 3), ("v", "<f4", (3, 3)), ("attr", "<u2")]))
    record["n"] = normal
    record["v"] = tri
    head = header.encode("ascii", "replace")[:80].ljust(80, b" ")
    return head + struct.pack("<I", len(faces)) + record.tobytes()


def obj_bytes(vertices: np.ndarray, faces: np.ndarray, comments: list[str]) -> bytes:
    lines = ["# " + " ".join(c.split()) for c in comments]
    lines += [f"v {float(x)!r} {float(y)!r} {float(z)!r}" for x, y, z in vertices]
    lines += [f"f {a + 1} {b + 1} {c + 1}" for a, b, c in faces]
    return ("\n".join(lines) + "\n").encode("utf-8")


def render(spec: Spec, limits: Limits = Limits()) -> tuple[bytes, tuple[str, ...]]:
    vertices, faces = mesh_of(spec)
    if len(faces) > min(MAX_TRIANGLES, limits.max_elements):
        raise Refused("the mesh would have more triangles than allowed", rule="max_elements", reason="too_large")
    unit = spec.options.get("unit", "mm")
    fmt = spec.options.get("format", "stl")
    if fmt == "stl":
        data = stl_bytes(vertices, faces, f"NeuroSciFi mesh; unit={unit}; form={spec.spec}; not evidence")
    else:
        data = obj_bytes(vertices, faces, ["NeuroSciFi mesh, made by the system from a specification; not evidence",
                                           f"unit: {unit}", f"form: {spec.spec}", f"title: {spec.title[:200]}"])
    lo, hi = vertices.min(axis=0), vertices.max(axis=0)
    notes = ("made by the system from a specification; not evidence",
             f"{len(faces)} triangles, {len(vertices)} vertices, unit {unit}",
             "size " + " by ".join(f"{float(v):.6g}" for v in hi - lo) + f" {unit}")
    return data, notes


def media_type(spec: Spec) -> str:
    return "model/obj" if spec.options.get("format", "stl") == "obj" else "model/stl"


__all__ = ["SCHEMAS", "UNITS", "box", "default_marker", "extrude", "markers", "media_type", "mesh_of", "obj_bytes",
           "render", "revolve", "stl_bytes", "surface", "triangulate"]
