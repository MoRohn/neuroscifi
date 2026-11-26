"""An independent check of meshes (DESIGN §6, ADR-074 lane G2): its own STL (binary and ASCII) and Wavefront OBJ
readers, then geometry measured from the triangles alone. It imports nothing from `make`.

Technical findings (they decide `ok`):
- `mesh_readable`: the bytes parse (binary STL: the triangle count matches the length exactly).
- `no_degenerate_faces`: no triangle repeats a vertex or has (almost) no area.
- `watertight`: for a solid, every edge is shared by exactly two triangles; for an open surface, no edge by more.
- `consistent_orientation`: every directed edge is used once, so neighbouring triangles agree on their winding (an
  inverted face uses an edge in the same direction as its neighbour and is caught here).
- `outward`: a closed solid's signed volume is positive (normals point out).
- `normals_agree` (STL): stored normals point the way the winding says (zero normals are allowed).
With a spec, the expectations are derived from the spec by the forms' reading contracts (make/solids.py states
them; this module re-derives them on its own): `unit`, `triangle_count`, `bounds` (within 1e-5 of the size),
`volume` (the analytic value where the form has one: box, extrusion, the faceted solid of revolution, the closed
surface, the markers; within 1e-5 relative), and `values_recovered` for a surface (each grid value read back from
the vertex at its grid point) and for points (each point read back as the centre of one marker).
"""
from __future__ import annotations

import math
import re
import struct

import numpy as np

from ..types import Limits
from . import Finding, Verdict

REL = 1e-5
MAX_LINE_COUNT = 10_000_000


class MeshError(ValueError):
    pass


def read_stl(data: bytes, limits: Limits = Limits()):
    """(vertices float64 (3m, 3), faces (m, 3), stored normals (m, 3), header text)."""
    if len(data) >= 84:
        count = struct.unpack_from("<I", data, 80)[0]
        if 84 + 50 * count == len(data):
            if count > limits.max_elements:
                raise MeshError("more triangles than the element limit")
            rec = np.frombuffer(data, dtype=np.dtype([("n", "<f4", 3), ("v", "<f4", (3, 3)), ("a", "<u2")]),
                                count=count, offset=84)
            vertices = rec["v"].reshape(-1, 3).astype(np.float64)
            faces = np.arange(3 * count, dtype=np.int64).reshape(-1, 3)
            return vertices, faces, rec["n"].astype(np.float64), data[:80].decode("latin-1")
    head = data[:5].lower()
    if head == b"solid" and b"facet" in data[:4096]:
        return _read_ascii_stl(data, limits)
    if len(data) >= 84:
        raise MeshError(f"a binary STL of {struct.unpack_from('<I', data, 80)[0]} triangles must be "
                        f"{84 + 50 * struct.unpack_from('<I', data, 80)[0]} bytes long, not {len(data)}")
    raise MeshError("too short to be an STL file")


_FLOAT = r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?"
_FACET = re.compile(rb"facet\s+normal\s+(" + _FLOAT.encode() + rb")\s+(" + _FLOAT.encode() + rb")\s+("
                    + _FLOAT.encode() + rb")\s+outer\s+loop((?:\s+vertex(?:\s+" + _FLOAT.encode()
                    + rb"){3}){3})\s+endloop\s+endfacet")


def _read_ascii_stl(data: bytes, limits: Limits):
    normals, verts = [], []
    for m in _FACET.finditer(data):
        if len(normals) >= limits.max_elements:
            raise MeshError("more triangles than the element limit")
        normals.append([float(m.group(i)) for i in (1, 2, 3)])
        nums = [float(x) for x in re.findall(_FLOAT.encode(), m.group(4))]
        verts.extend([nums[0:3], nums[3:6], nums[6:9]])
    if not normals:
        raise MeshError("an ASCII STL without a facet")
    v = np.array(verts, dtype=np.float64)
    if not np.isfinite(v).all():
        raise MeshError("a coordinate is not a finite number")
    return v, np.arange(len(v), dtype=np.int64).reshape(-1, 3), np.array(normals), data[:80].decode("latin-1")


def read_obj(data: bytes, limits: Limits = Limits()):
    """(vertices, faces, comments). Polygons are cut into fans; texture and normal indices are ignored."""
    vertices: list[tuple[float, float, float]] = []
    faces: list[tuple[int, int, int]] = []
    comments: list[str] = []
    text = data.decode("utf-8", "replace")
    for n, line in enumerate(text.splitlines()):
        if n > MAX_LINE_COUNT or len(vertices) + len(faces) > limits.max_elements:
            raise MeshError("more lines or elements than the limit")
        parts = line.split()
        if not parts:
            continue
        if parts[0].startswith("#"):
            comments.append(line.lstrip("# ").strip()[:200])
        elif parts[0] == "v":
            try:
                xyz = tuple(float(p) for p in parts[1:4])
            except ValueError:
                raise MeshError(f"line {n + 1}: a vertex is not three numbers") from None
            if len(xyz) != 3 or not all(math.isfinite(c) for c in xyz):
                raise MeshError(f"line {n + 1}: a vertex is not three finite numbers")
            vertices.append(xyz)
        elif parts[0] == "f":
            idx = []
            for p in parts[1:]:
                try:
                    i = int(p.split("/")[0])
                except ValueError:
                    raise MeshError(f"line {n + 1}: a face index is not a number") from None
                i = i - 1 if i > 0 else len(vertices) + i
                if not 0 <= i < len(vertices):
                    raise MeshError(f"line {n + 1}: a face refers to a vertex that does not exist")
                idx.append(i)
            if len(idx) < 3:
                raise MeshError(f"line {n + 1}: a face has fewer than three corners")
            faces.extend((idx[0], idx[k], idx[k + 1]) for k in range(1, len(idx) - 1))
    if not faces:
        raise MeshError("no faces")
    return np.array(vertices, dtype=np.float64), np.array(faces, dtype=np.int64), comments


def weld(vertices: np.ndarray, faces: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Merge vertices with exactly equal coordinates (an STL repeats each corner per triangle)."""
    unique, inverse = np.unique(vertices, axis=0, return_inverse=True)
    return unique, inverse.reshape(-1)[faces]


# ---------------------------------------------------------------------------------------------------------------
# What the spec calls for, re-derived from the forms' reading contracts


def _shoelace(ring) -> float:
    a = np.asarray(ring, dtype=np.float64)
    return 0.5 * float(np.dot(a[:, 0], np.roll(a[:, 1], -1)) - np.dot(np.roll(a[:, 0], -1), a[:, 1]))


def expectations(spec) -> dict | None:
    """Unit, triangle count, bounds, volume, whether it is a closed solid, and values to recover."""
    form = getattr(spec, "spec", None)
    d = spec.data if form else {}
    unit = spec.options.get("unit", "mm") if form else None
    if form == "solid.box":
        s = np.asarray(d["size"], dtype=np.float64)
        c = np.asarray(d.get("center", [0, 0, 0]), dtype=np.float64)
        return {"unit": unit, "closed": True, "triangles": 12, "lo": c - s / 2, "hi": c + s / 2,
                "volume": float(np.prod(s))}
    if form == "solid.extrude":
        holes = d.get("holes", [])
        out = np.asarray(d["outline"], dtype=np.float64)
        base, h = float(d.get("base_z", 0.0)), float(d["height"])
        n_pts = len(out) + sum(len(x) for x in holes)
        area = abs(_shoelace(out)) - sum(abs(_shoelace(x)) for x in holes)
        return {"unit": unit, "closed": True, "triangles": 2 * (n_pts + 2 * len(holes) - 2) + 2 * n_pts,
                "lo": np.array([*out.min(axis=0), base]), "hi": np.array([*out.max(axis=0), base + h]),
                "volume": area * h}
    if form == "solid.revolve":
        n = int(spec.options.get("segments", 64))
        p = [(float(r), float(z)) for r, z in d["profile"]]
        ends_on_axis = (p[0][0] == 0, p[-1][0] == 0)
        bands = sum(n if (a[0] == 0 or b[0] == 0) else 2 * n for a, b in zip(p, p[1:], strict=False))
        caps = n * ((not ends_on_axis[0]) + (not ends_on_axis[1]))
        integral = sum((b[1] - a[1]) * (a[0] ** 2 + a[0] * b[0] + b[0] ** 2) / 3.0 for a, b in zip(p, p[1:],
                                                                                                    strict=False))
        rmax = max(r for r, _ in p)
        ang = 2 * math.pi * np.arange(n) / n
        zs = [z for _, z in p]
        return {"unit": unit, "closed": True, "triangles": bands + caps,
                "lo": np.array([rmax * np.cos(ang).min(), rmax * np.sin(ang).min(), min(zs)]),
                "hi": np.array([rmax * np.cos(ang).max(), rmax * np.sin(ang).max(), max(zs)]),
                "volume": abs(0.5 * n * math.sin(2 * math.pi / n) * integral),
                "smooth_volume": abs(math.pi * integral)}
    if form == "surface.grid":
        z = np.asarray(d["values"], dtype=np.float64)
        ny, nx = z.shape
        xs = np.linspace(d["x"][0], d["x"][1], nx)
        ys = np.linspace(d["y"][0], d["y"][1], ny)
        out = {"unit": unit, "closed": "base_z" in d, "triangles": 2 * (nx - 1) * (ny - 1),
               "lo": np.array([d["x"][0], d["y"][0], z.min()]), "hi": np.array([d["x"][1], d["y"][1], z.max()]),
               "grid": (xs, ys, z)}
        if "base_z" in d:
            base = float(d["base_z"])
            rim = 2 * ((nx - 1) + (ny - 1))
            out["triangles"] += 3 * rim
            out["lo"][2] = base
            cell = (xs[1] - xs[0]) * (ys[1] - ys[0]) / 2.0     # each triangle's area seen from above
            h = z - base
            lower = (h[:-1, :-1] + h[:-1, 1:] + h[1:, 1:]) / 3.0      # (x low, y low), (x high, y low), (x high, y high)
            upper = (h[:-1, :-1] + h[1:, 1:] + h[1:, :-1]) / 3.0      # (x low, y low), (x high, y high), (x low, y high)
            out["volume"] = float(cell * (lower.sum() + upper.sum()))
        return out
    if form == "solid.points":
        p = np.asarray(d["points"], dtype=np.float64)
        s = spec.options.get("marker")
        if s is None:
            extent = float((p.max(axis=0) - p.min(axis=0)).max()) if len(p) > 1 else 0.0
            s = extent / 100.0 if extent > 0 else 1.0
        return {"unit": unit, "closed": True, "triangles": 8 * len(p), "lo": p.min(axis=0) - s,
                "hi": p.max(axis=0) + s, "volume": len(p) * 4.0 / 3.0 * s ** 3, "points": p}
    return None


# ---------------------------------------------------------------------------------------------------------------
# The check


def _unit_of(text: str) -> str | None:
    m = re.search(r"unit\s*[=:]\s*([A-Za-z]+)", text)
    return m.group(1) if m else None


def check_mesh(data: bytes, media_type: str = "model/stl", spec=None, source=None) -> Verdict:
    technical: list[Finding] = []
    style: list[Finding] = []
    kind = "obj" if media_type.split(";")[0].strip().lower() in ("model/obj", "text/x-wavefront-obj") else "stl"
    try:
        if kind == "stl":
            raw_v, raw_f, normals, header = read_stl(data)
            unit_text = header
        else:
            raw_v, raw_f, comments = read_obj(data)
            normals, unit_text = None, "\n".join(comments)
    except MeshError as error:
        return Verdict.of([Finding("mesh_readable", False, str(error))])
    technical.append(Finding("mesh_readable", True, f"{len(raw_f)} triangles read"))
    v, f = weld(raw_v, raw_f)
    tri = v[f]
    cross = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    area2 = np.linalg.norm(cross, axis=1)
    size = float((v.max(axis=0) - v.min(axis=0)).max()) if len(v) else 0.0
    repeated = (f[:, 0] == f[:, 1]) | (f[:, 1] == f[:, 2]) | (f[:, 0] == f[:, 2])
    flat = area2 <= 1e-12 * max(size, 1e-300) ** 2
    bad = int((repeated | flat).sum())
    technical.append(Finding("no_degenerate_faces", bad == 0, f"{bad} triangle(s) repeat a corner or have no area",
                             measure=float(bad)))
    directed = np.concatenate([f[:, [0, 1]], f[:, [1, 2]], f[:, [2, 0]]])
    undirected = np.sort(directed, axis=1)
    _, use = np.unique(undirected, axis=0, return_counts=True)
    _, dir_use = np.unique(directed, axis=0, return_counts=True)
    e = expectations(spec) if spec is not None else None
    closed = e["closed"] if e else bool((use == 2).all())
    if closed:
        tight = bool((use == 2).all())
        technical.append(Finding("watertight", tight, f"{int((use == 1).sum())} edge(s) on an opening, "
                                                      f"{int((use > 2).sum())} shared by more than two triangles",
                                 measure=float((use != 2).sum())))
    else:
        tight = bool((use <= 2).all())
        technical.append(Finding("watertight", tight, f"an open surface with {int((use == 1).sum())} edge(s) on its "
                                                      f"rim and {int((use > 2).sum())} shared by more than two"))
    consistent = bool((dir_use == 1).all())
    technical.append(Finding("consistent_orientation", consistent,
                             f"{int((dir_use > 1).sum())} directed edge(s) used twice (a face wound against its "
                             "neighbours)", measure=float((dir_use > 1).sum())))
    volume = float(np.einsum("ij,ij->i", tri[:, 0], np.cross(tri[:, 1], tri[:, 2])).sum()) / 6.0
    if closed:
        technical.append(Finding("outward", volume > 0, f"signed volume {volume:.6g}: normals point "
                                 + ("out" if volume > 0 else "in"), measure=volume))
    else:
        up = int((cross[:, 2] > 0).sum())
        technical.append(Finding("outward", up == len(f), f"{up} of {len(f)} triangles of the surface face up"))
    if normals is not None:
        stored = normals
        given = np.linalg.norm(stored, axis=1) > 0
        agree = np.einsum("ij,ij->i", stored, cross) > 0
        against = int((given & ~agree).sum())
        technical.append(Finding("normals_agree", against == 0, f"{against} stored normal(s) point against the "
                                                                "winding", measure=float(against)))
    if e is None:
        style.append(Finding("values_not_compared", True, "no solid spec was given, so nothing was compared"))
        return Verdict.of(technical, style)
    unit = _unit_of(unit_text)
    technical.append(Finding("unit", unit == e["unit"], f"the file states the unit {unit!r}; the spec says "
                                                        f"{e['unit']!r}"))
    technical.append(Finding("triangle_count", len(f) == e["triangles"],
                             f"{len(f)} triangles; the form calls for {e['triangles']}", measure=float(len(f))))
    lo, hi = v.min(axis=0), v.max(axis=0)
    span = max(float((e["hi"] - e["lo"]).max()), 1e-300)
    off = float(max(np.abs(lo - e["lo"]).max(), np.abs(hi - e["hi"]).max())) / span
    technical.append(Finding("bounds", off <= REL, "bounds " + _box(lo, hi) + "; the spec calls for "
                             + _box(e["lo"], e["hi"]), measure=off))
    if "volume" in e:
        rel = abs(volume - e["volume"]) / max(abs(e["volume"]), 1e-300)
        detail = f"volume {volume:.8g}; the analytic value is {e['volume']:.8g}"
        if "smooth_volume" in e:
            detail += f" (the smooth solid would hold {e['smooth_volume']:.8g})"
        technical.append(Finding("volume", rel <= REL, detail, measure=rel))
    if "grid" in e:
        xs, ys, z = e["grid"]
        found = _lookup(v)
        hits = 0
        for i, y in enumerate(ys):
            for j, x in enumerate(xs):
                got = found(x, y, z[i, j], span)
                hits += got is not None and abs(got - z[i, j]) <= REL * span
        share = hits / z.size
        technical.append(Finding("values_recovered", hits == z.size, f"{hits} of {z.size} grid values read back "
                                                                     "from the surface", measure=share))
    if "points" in e:
        share = _centres_recovered(v, f, e["points"], span)
        technical.append(Finding("values_recovered", share == 1.0, f"{share:.1%} of the points read back as marker "
                                                                   "centres", measure=share))
    return Verdict.of(technical, style)


def _box(lo, hi) -> str:
    return "[" + ", ".join(f"{a:.6g}..{b:.6g}" for a, b in zip(lo, hi, strict=True)) + "]"


def _lookup(v: np.ndarray):
    """Find the top vertex (largest z) at a grid point (x, y), within a tolerance of the size."""
    order = np.lexsort((v[:, 2], v[:, 1], v[:, 0]))
    sv = v[order]

    def find(x, y, z_hint, span):
        tol = REL * span
        lo = np.searchsorted(sv[:, 0], x - tol, side="left")
        hi = np.searchsorted(sv[:, 0], x + tol, side="right")
        cand = sv[lo:hi]
        cand = cand[np.abs(cand[:, 1] - y) <= tol]
        if not len(cand):
            return None
        return float(cand[np.argmin(np.abs(cand[:, 2] - z_hint))][2])
    return find


def _centres_recovered(v: np.ndarray, f: np.ndarray, points: np.ndarray, span: float) -> float:
    """Group triangles into connected pieces and compare each piece's vertex centre with the points."""
    parent = np.arange(len(v))

    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    for a, b, c in f:
        ra, rb, rc = root(a), root(b), root(c)
        parent[rb] = ra
        parent[root(rc)] = ra
    roots = np.array([root(i) for i in range(len(v))])
    _, label = np.unique(roots, return_inverse=True)
    label = label.reshape(-1)
    count = np.bincount(label).astype(np.float64)
    centres = np.column_stack([np.bincount(label, weights=v[:, k]) / count for k in range(3)])
    if len(centres) != len(points):
        return 0.0
    a = centres[np.lexsort(centres.T[::-1])]
    b = points[np.lexsort(points.T[::-1])]
    return float((np.abs(a - b).max(axis=1) <= REL * span + 1e-6 * np.abs(b).max(axis=1)).mean())


__all__ = ["MeshError", "check_mesh", "expectations", "read_obj", "read_stl", "weld"]
