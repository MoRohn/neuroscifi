"""Measurements computed from decoded `Features`. Each is a named function with a stated method.

- counts by geometry type, number of coordinates;
- bounding box over x/y (longitude/latitude for geographic data), and over elevation when present;
- length on the sphere: the haversine distance between consecutive points of each line (LineString,
  MultiLineString parts, Polygon and MultiPolygon rings), mean Earth radius R = 6 371 008.8 m; computed only when
  the coordinate system is geographic longitude/latitude (OGC:CRS84, EPSG:4326, CRS84 URNs), else None with the
  reason;
- time span: the earliest and latest timestamp (`times_s`) over all features, and their difference.
"""
from __future__ import annotations

import math

import numpy as np

from .types import Features

EARTH_RADIUS_M = 6_371_008.8
GEOGRAPHIC = ("OGC:CRS84", "EPSG:4326", "URN:OGC:DEF:CRS:OGC:1.3:CRS84", "URN:OGC:DEF:CRS:EPSG::4326")
LINEAR = {"LineString", "MultiLineString", "Polygon", "MultiPolygon"}


def counts(f: Features) -> dict:
    by: dict[str, int] = {}
    for feat in f.features:
        by[feat.geometry] = by.get(feat.geometry, 0) + 1
    return {"features": len(f.features), "by_geometry": by,
            "coordinates": int(sum(len(x.coordinates) for x in f.features))}


def bounding_box(f: Features) -> dict | None:
    coords = [x.coordinates for x in f.features if len(x.coordinates)]
    if not coords:
        return None
    xy = np.concatenate([c[:, :2] for c in coords])
    out = {"min_x": float(xy[:, 0].min()), "min_y": float(xy[:, 1].min()), "max_x": float(xy[:, 0].max()),
           "max_y": float(xy[:, 1].max())}
    z = np.concatenate([c[:, 2] for c in coords if c.shape[1] == 3]) if any(c.shape[1] == 3 for c in coords) else None
    if z is not None and np.isfinite(z).any():
        out["min_z"], out["max_z"] = float(np.nanmin(z)), float(np.nanmax(z))
    return out


def haversine_m(lonlat: np.ndarray) -> float:
    """Length in metres of a polyline of (longitude, latitude) degrees on a sphere of EARTH_RADIUS_M."""
    if len(lonlat) < 2:
        return 0.0
    lon, lat = np.radians(lonlat[:, 0]), np.radians(lonlat[:, 1])
    dlat, dlon = np.diff(lat), np.diff(lon)
    a = np.sin(dlat / 2) ** 2 + np.cos(lat[:-1]) * np.cos(lat[1:]) * np.sin(dlon / 2) ** 2
    return float((2 * EARTH_RADIUS_M * np.arcsin(np.sqrt(np.clip(a, 0, 1)))).sum())


def _parts(feat) -> list[np.ndarray]:
    sizes = feat.properties.get("_parts")
    if not sizes:
        return [feat.coordinates]
    out, at = [], 0
    for n in sizes:
        out.append(feat.coordinates[at:at + int(n)])
        at += int(n)
    return out


def is_geographic(crs: str | None) -> bool:
    return bool(crs) and crs.strip().upper() in GEOGRAPHIC


def lengths_m(f: Features) -> dict:
    """Per line feature and in total, metres on the sphere; None with a reason for non-geographic data."""
    if not is_geographic(f.crs):
        return {"total_m": None, "reason": f"the coordinate system {f.crs!r} is not geographic longitude/latitude"}
    per = []
    for i, feat in enumerate(f.features):
        if feat.geometry in LINEAR:
            per.append({"feature": i, "geometry": feat.geometry,
                        "length_m": sum(haversine_m(p[:, :2]) for p in _parts(feat))})
    return {"total_m": float(sum(p["length_m"] for p in per)), "per_feature": per[:64], "n_line_features": len(per)}


def time_span(f: Features) -> dict | None:
    times = [t for feat in f.features for t in feat.properties.get("times_s", ()) if not math.isnan(t)]
    if not times:
        return None
    return {"start_s": min(times), "end_s": max(times), "duration_s": max(times) - min(times), "n_times": len(times)}


def analyse(f: Features) -> dict:
    return {**counts(f), "crs": f.crs, "bounding_box": bounding_box(f), "lengths": lengths_m(f),
            "time_span": time_span(f)}


__all__ = ["EARTH_RADIUS_M", "analyse", "bounding_box", "counts", "haversine_m", "is_geographic", "lengths_m",
           "time_span"]
