"""Measurements computed from a decoded `Raster`'s pixels. Each is a named function with a stated method; nothing
here says what an image depicts, and whether it "looks like" a chart, text or a photograph is NOT claimed.

Values are normalised to [0, 1] by the raster's bit depth (v / (2^bits - 1)). Luminance is Rec. 709
(0.2126 R + 0.7152 G + 0.0722 B) on the stored values (no gamma decoding). `analyse()` gathers everything into a
bounded, JSON-able dict.
"""
from __future__ import annotations

import numpy as np

from .types import Raster

REGION_MAX_PIXELS = 1 << 20      # connected regions are computed only on images up to this many pixels
REGION_LIST = 32
EDGE_THRESHOLD = 0.25            # Sobel gradient magnitude (on [0, 1] luminance) counted as an edge
QUANT_LEVELS = 4                 # dominant colours: each channel quantised to this many levels


def normalised(r: Raster) -> np.ndarray:
    """float64 (h, w, c) in [0, 1]."""
    top = float((1 << r.bits) - 1)
    p = r.pixels.astype(np.float64) / top
    return p[:, :, None] if p.ndim == 2 else p


def size_mode(r: Raster) -> dict:
    w, h = r.size
    return {"width": w, "height": h, "mode": r.mode, "bits": r.bits, "pixels": w * h}


def channel_stats(r: Raster) -> list[dict]:
    """Per channel min, max, mean, standard deviation, in [0, 1]."""
    n = normalised(r)
    p = n.reshape(-1, n.shape[2])
    names = {"gray": ("gray",), "rgb": ("red", "green", "blue"), "rgba": ("red", "green", "blue", "alpha")}[r.mode]
    out = []
    for i, name in enumerate(names):
        c = p[:, i] if p.size else np.zeros(1)
        out.append({"channel": name, "min": float(c.min()), "max": float(c.max()), "mean": float(c.mean()),
                    "std": float(c.std())})
    return out


def luminance(r: Raster) -> np.ndarray:
    """(h, w) float64 in [0, 1]: Rec. 709 weights for colour, the grey value for grey."""
    p = normalised(r)
    if r.mode == "gray":
        return p[:, :, 0]
    return p[:, :, 0] * 0.2126 + p[:, :, 1] * 0.7152 + p[:, :, 2] * 0.0722


def luminance_histogram(r: Raster, bins: int = 16) -> list[int]:
    """Pixel counts in `bins` equal-width luminance bins over [0, 1]."""
    counts, _ = np.histogram(luminance(r), bins=bins, range=(0.0, 1.0))
    return [int(c) for c in counts]


def transparent_share(r: Raster) -> float:
    """Share of pixels whose alpha is 0 (0.0 for images without alpha)."""
    if r.mode != "rgba" or not r.pixels.size:
        return 0.0
    return float((r.pixels[:, :, 3] == 0).mean())


def dominant_colours(r: Raster, k: int = 5, levels: int = QUANT_LEVELS) -> list[dict]:
    """The `k` most frequent colours after quantising each colour channel to `levels` levels (fully transparent
    pixels excluded). Each: the bin's centre as 0-255 values per channel, and its share of counted pixels."""
    p = normalised(r)
    colour = p[:, :, :1] if r.mode == "gray" else p[:, :, :3]
    flat = colour.reshape(-1, colour.shape[2])
    if r.mode == "rgba":
        flat = flat[r.pixels.reshape(-1, 4)[:, 3] > 0]
    if not len(flat):
        return []
    q = np.minimum((flat * levels).astype(np.int64), levels - 1)
    keys = np.zeros(len(q), np.int64)
    for i in range(q.shape[1]):
        keys = keys * levels + q[:, i]
    values, counts = np.unique(keys, return_counts=True)
    order = np.argsort(-counts, kind="stable")[:k]
    out = []
    for idx in order:
        key, digits = int(values[idx]), []
        for _ in range(q.shape[1]):
            digits.append(key % levels)
            key //= levels
        centre = [int(round((d + 0.5) / levels * 255)) for d in reversed(digits)]
        out.append({"colour": centre, "share": float(counts[idx] / len(q))})
    return out


def sobel_magnitude(r: Raster) -> np.ndarray:
    """(h, w) gradient magnitude of the luminance with the 3x3 Sobel kernels (edges replicated)."""
    y = np.pad(luminance(r), 1, mode="edge")
    gx = (y[:-2, 2:] + 2 * y[1:-1, 2:] + y[2:, 2:]) - (y[:-2, :-2] + 2 * y[1:-1, :-2] + y[2:, :-2])
    gy = (y[2:, :-2] + 2 * y[2:, 1:-1] + y[2:, 2:]) - (y[:-2, :-2] + 2 * y[:-2, 1:-1] + y[:-2, 2:])
    return np.hypot(gx, gy)


def edge_density(r: Raster, threshold: float = EDGE_THRESHOLD) -> float:
    """Share of pixels whose Sobel gradient magnitude exceeds `threshold`."""
    if not r.pixels.size:
        return 0.0
    return float((sobel_magnitude(r) > threshold).mean())


def regions(r: Raster, threshold: float = 0.5, *, above: bool = True, max_pixels: int = REGION_MAX_PIXELS) -> dict:
    """4-connected regions of pixels whose luminance is above (or below) `threshold`, on images of at most
    `max_pixels` pixels: their number and the bounding boxes (x0, y0, x1, y1 inclusive) and pixel counts of the
    largest REGION_LIST. Larger images: {"skipped": reason}."""
    w, h = r.size
    if w * h > max_pixels:
        return {"skipped": f"the image has more than {max_pixels} pixels"}
    mask = luminance(r) > threshold if above else luminance(r) < threshold
    parent: list[int] = []

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    runs = []          # (row, start, end_exclusive, label)
    prev: list[tuple[int, int, int]] = []
    for y in range(h):
        row = np.concatenate([[False], mask[y], [False]])
        edges = np.flatnonzero(row[1:] != row[:-1])
        cur = []
        j = 0
        for s, e in zip(edges[::2].tolist(), edges[1::2].tolist()):
            label = len(parent)
            parent.append(label)
            while j < len(prev) and prev[j][1] <= s:
                j += 1
            k = j
            while k < len(prev) and prev[k][0] < e:
                a, b = find(prev[k][2]), find(label)
                if a != b:
                    parent[max(a, b)] = min(a, b)
                k += 1
            cur.append((s, e, label))
            runs.append((y, s, e, label))
        prev = cur
    boxes: dict[int, list[int]] = {}
    for y, s, e, label in runs:
        root = find(label)
        box = boxes.get(root)
        if box is None:
            boxes[root] = [s, y, e - 1, y, e - s]
        else:
            box[0], box[1], box[2], box[3] = min(box[0], s), min(box[1], y), max(box[2], e - 1), max(box[3], y)
            box[4] += e - s
    ordered = sorted(boxes.values(), key=lambda b: (-b[4], b[1], b[0]))
    return {"count": len(ordered), "threshold": threshold, "above": above,
            "largest": [{"box": b[:4], "pixels": b[4]} for b in ordered[:REGION_LIST]]}


def analyse(r: Raster) -> dict:
    return {**size_mode(r), "channels": channel_stats(r), "luminance_histogram": luminance_histogram(r),
            "transparent_share": transparent_share(r), "dominant_colours": dominant_colours(r),
            "edge_density": edge_density(r), "regions": regions(r)}


__all__ = ["EDGE_THRESHOLD", "REGION_MAX_PIXELS", "analyse", "channel_stats", "dominant_colours", "edge_density",
           "luminance", "luminance_histogram", "normalised", "regions", "size_mode", "sobel_magnitude",
           "transparent_share"]
