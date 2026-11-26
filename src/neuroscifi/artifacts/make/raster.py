"""Raster images from a numeric grid (DESIGN §6, ADR-074 lane G2): `image.raster` (PNG) and `image.apng` (animated
PNG from a stack of grids).

The reading contract of a raster (the checker re-derives it from the spec alone):
- each grid cell is a square block of `cell_px` pixels (default: the largest whole number that keeps the longer
  side within 512 pixels, at least 1); row 0 of the grid is the top row of the image;
- a value v maps to a gray level linearly over `range` [lo, hi] (default: the smallest and largest present value,
  over every frame of an animation): `dark_low` (the default) puts lo at black and hi at white, `light_low` the
  reverse; values outside the range are shown at its ends; a flat range (lo = hi) is the middle gray;
- 8 or 16 bits per sample (`bits`);
- a missing value (null) is a fully transparent cell;
- a cell whose value is above `warn_above` or below `warn_below` gets an amber outline (the only hue, DESIGN §6) of
  max(1, cell_px // 6) pixels; its centre keeps its gray, so the value stays readable. Outlines need cells of at
  least 3 pixels.
No text is drawn (there is no font here): the title, the description and the scale in words travel in iTXt chunks.
Nothing made here is evidence (COMMON, ADR-049, ADR-071).
"""
from __future__ import annotations

import math

import numpy as np

from ..spec import Spec
from ..types import Limits, Malformed, Refused
from . import png_write

AMBER = (255, 176, 0)
MAX_SIDE = 1024
MAX_FRAMES = 240
TARGET_PX = 512
_CELL = {"type": ["number", "null"], "minimum": -1e300, "maximum": 1e300}
_GRID = {"type": "array", "minItems": 1, "maxItems": MAX_SIDE,
         "items": {"type": "array", "minItems": 1, "maxItems": MAX_SIDE, "items": _CELL}}
_OPTIONS = {
    "range": {"type": "array", "minItems": 2, "maxItems": 2, "items": {"type": "number"}},
    "ramp": {"type": "string", "enum": ["dark_low", "light_low"]},
    "cell_px": {"type": "integer", "minimum": 1, "maximum": 64},
    "bits": {"type": "integer", "enum": [8, 16]},
    "warn_above": {"type": "number"},
    "warn_below": {"type": "number"},
    "unit": {"type": "string", "maxLength": 32},
    "history": {"type": "array", "maxItems": 3, "items": {"type": "string", "maxLength": 300}},
}
SCHEMAS = {
    "image.raster": {
        "data": {"type": "object", "additionalProperties": False, "required": ["grid"], "properties": {"grid": _GRID}},
        "options": {"type": "object", "additionalProperties": False, "properties": _OPTIONS}},
    "image.apng": {
        "data": {"type": "object", "additionalProperties": False, "required": ["frames", "delays_ms"],
                 "properties": {"frames": {"type": "array", "minItems": 1, "maxItems": MAX_FRAMES, "items": _GRID},
                                "delays_ms": {"type": "array", "minItems": 1, "maxItems": MAX_FRAMES,
                                              "items": {"type": "integer", "minimum": 10, "maximum": 65535}}}},
        "options": {"type": "object", "additionalProperties": False,
                    "properties": {**_OPTIONS, "plays": {"type": "integer", "minimum": 0, "maximum": 1000}}}},
}


def _grid(rows) -> np.ndarray:
    if len({len(r) for r in rows}) != 1:
        raise Malformed("every row of the grid must have the same length")
    return np.array([[np.nan if v is None else float(v) for v in r] for r in rows], dtype=np.float64)


def default_cell(rows: int, cols: int) -> int:
    return max(1, TARGET_PX // max(rows, cols))


def _range(grids: list[np.ndarray], given) -> tuple[float, float]:
    if given is not None:
        lo, hi = float(given[0]), float(given[1])
        if hi < lo:
            raise Malformed("the range's first number must not exceed its second")
        return lo, hi
    present = np.concatenate([g[np.isfinite(g)] for g in grids])
    return (float(present.min()), float(present.max())) if present.size else (0.0, 1.0)


def _levels(g: np.ndarray, lo: float, hi: float, top: int, ramp: str) -> np.ndarray:
    if hi == lo:
        level = np.full(g.shape, top // 2, dtype=np.int64)
    else:
        share = np.clip((np.nan_to_num(g, nan=lo) - lo) / (hi - lo), 0.0, 1.0)
        level = np.round(share * top).astype(np.int64)
    return top - level if ramp == "light_low" else level


def picture(spec: Spec, grid: np.ndarray, lo: float, hi: float, cell: int, warn: bool, alpha: bool) -> np.ndarray:
    o = spec.options
    bits = int(o.get("bits", 8))
    top = (1 << bits) - 1
    dtype = np.uint16 if bits == 16 else np.uint8
    level = _levels(grid, lo, hi, top, o.get("ramp", "dark_low"))
    gray = np.kron(level, np.ones((cell, cell), dtype=np.int64))
    missing = np.kron(~np.isfinite(grid), np.ones((cell, cell), dtype=bool))
    channels = [gray] * (3 if warn else 1)
    if warn:
        flagged = np.zeros(grid.shape, dtype=bool)
        with np.errstate(invalid="ignore"):
            if "warn_above" in o:
                flagged |= grid > float(o["warn_above"])
            if "warn_below" in o:
                flagged |= grid < float(o["warn_below"])
        width = max(1, cell // 6)
        ring = np.ones((cell, cell), dtype=bool)
        ring[width:cell - width, width:cell - width] = False
        outline = np.kron(flagged, ring).astype(bool)
        scale = top / 255.0
        channels = [np.where(outline, int(round(c * scale)), gray) for c in AMBER]
    if alpha:
        channels.append(np.where(missing, 0, top))
    for ch in channels[: len(channels) - (1 if alpha else 0)]:
        ch[missing] = 0
    out = channels[0] if len(channels) == 1 else np.stack(channels, axis=2)
    return out.astype(dtype)


def scale_words(spec: Spec, lo: float, hi: float) -> str:
    unit = (" " + spec.options["unit"]) if spec.options.get("unit") else ""
    dark, light = (hi, lo) if spec.options.get("ramp") == "light_low" else (lo, hi)
    text = (f"Gray scale: black stands for {_fmt(dark)}{unit} and white for {_fmt(light)}{unit}, evenly in between; "
            "values outside that range are shown at its ends.")
    if lo == hi:
        text = f"Every value is {_fmt(lo)}{unit}, shown as middle gray."
    if "warn_above" in spec.options or "warn_below" in spec.options:
        parts = []
        if "warn_above" in spec.options:
            parts.append(f"above {_fmt(spec.options['warn_above'])}{unit}")
        if "warn_below" in spec.options:
            parts.append(f"below {_fmt(spec.options['warn_below'])}{unit}")
        text += " Cells " + " or ".join(parts) + " have an amber outline."
    return text + " A transparent cell is a missing value."


def _fmt(x) -> str:
    x = float(x)
    return str(int(x)) if x == int(x) and abs(x) < 1e15 else f"{x:.6g}"


def render(spec: Spec, limits: Limits = Limits()) -> tuple[bytes, tuple[str, ...]]:
    o = spec.options
    if spec.spec == "image.raster":
        grids = [_grid(spec.data["grid"])]
    else:
        grids = [_grid(g) for g in spec.data["frames"]]
        if len({g.shape for g in grids}) != 1:
            raise Malformed("every frame must have the same number of rows and columns")
    rows, cols = grids[0].shape
    cell = int(o.get("cell_px", default_cell(rows, cols)))
    warn = "warn_above" in o or "warn_below" in o
    if warn and cell < 3:
        raise Malformed("warning outlines need cells of at least 3 pixels")
    bits = int(o.get("bits", 8))
    alpha = any(bool(np.isnan(g).any()) for g in grids)
    channels = (3 if warn else 1) + (1 if alpha else 0)
    size = rows * cell * cols * cell * channels * (bits // 8) * len(grids)
    if size > limits.max_decoded_bytes or rows * cell > 16384 or cols * cell > 16384:
        raise Refused("the image would be larger than allowed", rule="max_decoded_bytes", reason="too_large")
    lo, hi = _range(grids, o.get("range"))
    pictures = [picture(spec, g, lo, hi, cell, warn, alpha) for g in grids]
    words = scale_words(spec, lo, hi)
    texts = [("Title", spec.title), ("Description", (spec.description + " " + words).strip()),
             ("Software", "NeuroSciFi; made by the system from a specification; not evidence")]
    detail = f"{cols * cell} by {rows * cell} pixels, {cell} pixel(s) per cell"
    if spec.spec == "image.raster":
        data = png_write.png(pictures[0], texts)
    else:
        delays = list(spec.data["delays_ms"])
        if len(delays) == 1:
            delays = delays * len(pictures)
        if len(delays) != len(pictures):
            raise Malformed("give one delay, or one delay per frame")
        data = png_write.apng(pictures, delays, int(o.get("plays", 0)), texts)
        detail += f", {len(pictures)} frames lasting {math.fsum(delays) / 1000:.3g} s"
    return data, ("made by the system from a specification; not evidence", words, detail)


def media_type(spec: Spec) -> str:
    return "image/apng" if spec.spec == "image.apng" else "image/png"


__all__ = ["AMBER", "SCHEMAS", "default_cell", "media_type", "picture", "render", "scale_words"]
