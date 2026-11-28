"""Measurements computed from a decoded `Mesh` (triangles). Each is a named function with a stated method.

- counts: vertices, triangles, vertices used by a triangle;
- bounds: axis-aligned min/max and size;
- surface area: the sum of triangle areas (half the cross product's length);
- signed volume: the divergence theorem, sum of det(a, b, c) / 6 over triangles; positive when the triangles are
  wound counter-clockwise seen from outside (outward normals); meaningful only for a closed surface;
- edge use: an undirected edge used by exactly two triangles is interior, by one a boundary, by more non-manifold;
  `watertight` = every edge used exactly twice; `manifold` = no edge used more than twice;
- consistent orientation: no directed edge appears twice (neighbours traverse a shared edge in opposite directions);
- degenerate triangles: a repeated vertex index, or an area at most 1e-12 x the squared bounding-box diagonal;
- connected components: triangles joined through shared vertices (union-find on vertex labels);
- Euler characteristic: V - E + F over the vertices used, the undirected edges and the triangles.
"""
from __future__ import annotations

import numpy as np

from .types import Mesh


def _edges(faces: np.ndarray) -> np.ndarray:
    return np.concatenate([faces[:, [0, 1]], faces[:, [1, 2]], faces[:, [2, 0]]])


def counts(m: Mesh) -> dict:
    used = np.unique(m.faces) if len(m.faces) else np.zeros(0, np.int64)
    return {"vertices": int(len(m.vertices)), "triangles": int(len(m.faces)), "vertices_used": int(len(used))}


def bounds(m: Mesh) -> dict | None:
    if not len(m.vertices):
        return None
    lo, hi = m.vertices.min(axis=0), m.vertices.max(axis=0)
    return {"min": lo.tolist(), "max": hi.tolist(), "size": (hi - lo).tolist()}


def triangle_areas(m: Mesh) -> np.ndarray:
    if not len(m.faces):
        return np.zeros(0)
    a, b, c = (m.vertices[m.faces[:, i]] for i in range(3))
    return 0.5 * np.linalg.norm(np.cross(b - a, c - a), axis=1)


def surface_area(m: Mesh) -> float:
    return float(triangle_areas(m).sum())


def signed_volume(m: Mesh) -> float:
    if not len(m.faces):
        return 0.0
    a, b, c = (m.vertices[m.faces[:, i]] for i in range(3))
    return float(np.einsum("ij,ij->i", a, np.cross(b, c)).sum() / 6.0)


def edge_use(m: Mesh) -> dict:
    if not len(m.faces):
        return {"edges": 0, "boundary": 0, "interior": 0, "non_manifold": 0, "watertight": False, "manifold": True}
    e = np.sort(_edges(m.faces), axis=1)
    _, n = np.unique(e, axis=0, return_counts=True)
    return {"edges": int(len(n)), "boundary": int((n == 1).sum()), "interior": int((n == 2).sum()),
            "non_manifold": int((n > 2).sum()), "watertight": bool((n == 2).all()), "manifold": bool((n <= 2).all())}


def consistently_oriented(m: Mesh) -> bool:
    if not len(m.faces):
        return True
    _, n = np.unique(_edges(m.faces), axis=0, return_counts=True)
    return bool((n == 1).all())


def degenerate(m: Mesh) -> int:
    if not len(m.faces):
        return 0
    f = m.faces
    repeated = (f[:, 0] == f[:, 1]) | (f[:, 1] == f[:, 2]) | (f[:, 0] == f[:, 2])
    b = bounds(m)
    diag2 = float(np.sum(np.square(b["size"]))) if b else 0.0
    tiny = triangle_areas(m) <= 1e-12 * max(diag2, 1e-300)
    return int((repeated | tiny).sum())


def components(m: Mesh) -> int:
    """Number of connected pieces of the triangle set (vertices joined by triangle edges; unused vertices ignored)."""
    if not len(m.faces):
        return 0
    used, f = np.unique(m.faces, return_inverse=True)
    f = f.reshape(-1, 3)
    label = np.arange(len(used))
    e = _edges(f)
    u, v = e[:, 0], e[:, 1]
    while True:
        lo = np.minimum(label[u], label[v])
        before = label.copy()
        np.minimum.at(label, u, lo)
        np.minimum.at(label, v, lo)
        while True:                     # pointer jumping: follow labels to their root
            nxt = label[label]
            if np.array_equal(nxt, label):
                break
            label = nxt
        if np.array_equal(label, before):
            break
    return int(len(np.unique(label)))


def euler_characteristic(m: Mesh) -> int:
    c = counts(m)
    return c["vertices_used"] - edge_use(m)["edges"] + c["triangles"]


def analyse(m: Mesh) -> dict:
    use = edge_use(m)
    return {**counts(m), "bounds": bounds(m), "unit": m.unit, "surface_area": surface_area(m),
            "signed_volume": signed_volume(m) if use["watertight"] else None, "edge_use": use,
            "consistently_oriented": consistently_oriented(m), "degenerate_triangles": degenerate(m),
            "components": components(m), "euler_characteristic": euler_characteristic(m)}


__all__ = ["analyse", "bounds", "components", "consistently_oriented", "counts", "degenerate", "edge_use",
           "euler_characteristic", "signed_volume", "surface_area", "triangle_areas"]
