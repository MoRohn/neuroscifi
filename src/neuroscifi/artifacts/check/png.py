"""An independent check of PNG and animated PNG (DESIGN §6, ADR-074 lane G2). It imports nothing from `make`.

Its own reader: the signature, every chunk's length and CRC-32, the chunk order rules (IHDR first, IEND last and
nothing after it, IDAT chunks together, acTL before the image data, fcTL/fdAT sequence numbers 0, 1, 2, ... without
gaps), the image data inflated with a bounded output (zlib's decoder for DEFLATE; everything else, including the
five scanline filters, is this module's own), and the frames of an animation composed by their dispose and blend
operations. Colour types 0, 2, 4 and 6 at 8 or 16 bits are read; palette images, lower bit depths and interlaced
images are recognised and reported as not read here, never guessed at.

Technical findings: `png_structure`, `crc`, `pixels_decoded`, `animation` (APNG). With a raster spec (the reading
contract in make/raster.py, re-derived here from the spec alone): `dimensions` (whole square cells), `frame_count`,
`delays`, `values_recovered` (each cell's centre pixel mapped back through the stated gray ramp to a value, compared
with the grid within half a gray step), `missing_marked` (missing values transparent, others opaque) and
`hue_only_on_warnings` (every pixel with hue lies on the outline of a cell the spec's thresholds flag, and every
flagged cell has one). Style: `description` (the file states its scale in words).
"""
from __future__ import annotations

import struct
import time
import zlib

import numpy as np

from ..types import Limits
from . import Finding, Verdict

SIGNATURE = b"\x89PNG\r\n\x1a\n"
KNOWN_CRITICAL = {b"IHDR", b"PLTE", b"IDAT", b"IEND"}
CHANNELS = {0: 1, 2: 3, 4: 2, 6: 4, 3: 1}
TARGET_PX = 512


class PngError(ValueError):
    pass


class _Frame:
    def __init__(self, width, height, x, y, delay_s, dispose, blend):
        self.width, self.height, self.x, self.y = width, height, x, y
        self.delay_s, self.dispose, self.blend = delay_s, dispose, blend
        self.data = bytearray()
        self.pixels = None


class Png:
    def __init__(self):
        self.width = self.height = self.depth = self.color = self.interlace = 0
        self.problems: list[str] = []
        self.crc_errors = 0
        self.texts: dict[str, str] = {}
        self.animated = False
        self.num_frames = 0
        self.plays = 0
        self.frames: list[_Frame] = []      # the animation's frames (APNG), composed onto the canvas
        self.image = None                   # the still image (IDAT)
        self.decoded = False


def _inflate(data: bytes, expected: int) -> bytes:
    """Inflate with the output bounded by the size the header implies (a flat image legitimately compresses
    1000:1, so the bound is the size, not a ratio)."""
    z = zlib.decompressobj()
    try:
        out = z.decompress(data, expected + 1)
    except zlib.error:
        raise PngError("the image data is not a valid zlib stream") from None
    if len(out) > expected:
        raise PngError("the image data inflates to more bytes than its size allows")
    if not z.eof:
        raise PngError("the image data stream is cut short")
    return out


def _unfilter(raw: bytes, width: int, height: int, bpp: int, deadline: float) -> np.ndarray:
    stride = width * bpp
    if len(raw) != height * (stride + 1):
        raise PngError(f"the image data holds {len(raw)} bytes; {height} rows of {stride + 1} were expected")
    rows = np.frombuffer(raw, dtype=np.uint8).reshape(height, stride + 1)
    out = np.zeros((height, stride), dtype=np.uint8)
    prior = np.zeros(stride, dtype=np.int64)
    for y in range(height):
        if time.monotonic() > deadline:
            raise PngError("reading the pixels took longer than allowed")
        kind, line = int(rows[y, 0]), rows[y, 1:].astype(np.int64)
        if kind == 0:
            cur = line
        elif kind == 1:      # Sub: a running sum along each byte position of a pixel
            cur = np.cumsum(line.reshape(width, bpp), axis=0).reshape(-1) & 0xFF
        elif kind == 2:      # Up
            cur = (line + prior) & 0xFF
        elif kind in (3, 4):
            cur = np.zeros(stride, dtype=np.int64)
            for i in range(stride):
                left = int(cur[i - bpp]) if i >= bpp else 0
                up = int(prior[i])
                if kind == 3:      # Average
                    pred = (left + up) >> 1
                else:              # Paeth
                    ul = int(prior[i - bpp]) if i >= bpp else 0
                    p = left + up - ul
                    pa, pb, pc = abs(p - left), abs(p - up), abs(p - ul)
                    pred = left if pa <= pb and pa <= pc else (up if pb <= pc else ul)
                cur[i] = (int(line[i]) + pred) & 0xFF
        else:
            raise PngError(f"row {y} uses the unknown filter type {kind}")
        out[y] = cur
        prior = cur
    return out


def _pixels(raw: bytes, width: int, height: int, depth: int, color: int, deadline: float) -> np.ndarray:
    channels = CHANNELS[color]
    bpp = channels * depth // 8
    flat = _unfilter(raw, width, height, bpp, deadline)
    if depth == 16:
        values = flat.reshape(height, width * channels, 2).astype(np.uint16)
        values = (values[:, :, 0] << 8) | values[:, :, 1]
    else:
        values = flat
    return values.reshape(height, width, channels)


def read(data: bytes, limits: Limits = Limits()) -> Png:
    p = Png()
    deadline = time.monotonic() + limits.max_seconds
    if len(data) > limits.max_bytes:
        p.problems.append("larger than the size limit")
        return p
    if data[:8] != SIGNATURE:
        p.problems.append("the PNG signature is missing")
        return p
    pos, seq_expected, idat = 8, 0, bytearray()
    seen: list[bytes] = []
    current: _Frame | None = None
    idat_done = False
    while True:
        if pos + 12 > len(data):
            p.problems.append("the file ends without IEND")
            break
        length = struct.unpack_from(">I", data, pos)[0]
        kind = data[pos + 4:pos + 8]
        if length > 0x7FFFFFFF or pos + 12 + length > len(data):
            p.problems.append(f"a chunk declares {length} bytes past the end of the file")
            break
        body = data[pos + 8:pos + 8 + length]
        crc = struct.unpack_from(">I", data, pos + 8 + length)[0]
        if zlib.crc32(kind + body) & 0xFFFFFFFF != crc:
            p.crc_errors += 1
        pos += 12 + length
        if not seen and kind != b"IHDR":
            p.problems.append("the first chunk is not IHDR")
            break
        if len(seen) >= 100_000:
            p.problems.append("too many chunks")
            break
        if seen and seen[-1] == b"IDAT" and kind != b"IDAT":
            idat_done = True
        seen.append(kind)
        if kind == b"IHDR":
            if len(seen) > 1 or length != 13:
                p.problems.append("IHDR is repeated or the wrong size")
                break
            p.width, p.height, p.depth, p.color, comp, filt, p.interlace = struct.unpack(">IIBBBBB", body)
            if p.width == 0 or p.height == 0 or comp != 0 or filt != 0 or p.color not in CHANNELS:
                p.problems.append("IHDR states an impossible image")
                break
        elif kind == b"IEND":
            if pos != len(data):
                p.problems.append(f"{len(data) - pos} bytes follow IEND")
            break
        elif kind == b"IDAT":
            if idat_done:
                p.problems.append("the IDAT chunks are not together")
            idat.extend(body)
        elif kind == b"acTL":
            if b"IDAT" in seen:
                p.problems.append("acTL comes after the image data")
            if length != 8:
                p.problems.append("acTL is the wrong size")
                break
            p.animated = True
            p.num_frames, p.plays = struct.unpack(">II", body)
        elif kind == b"fcTL":
            if length != 26:
                p.problems.append("fcTL is the wrong size")
                break
            seq, w, h, x, y, dn, dd, dispose, blend = struct.unpack(">IIIIIHHBB", body)
            if seq != seq_expected:
                p.problems.append(f"sequence number {seq} where {seq_expected} was due")
            seq_expected = seq + 1
            if w == 0 or h == 0 or x + w > p.width or y + h > p.height or dispose > 2 or blend > 1:
                p.problems.append("a frame lies outside the canvas or states an unknown operation")
                break
            current = _Frame(w, h, x, y, dn / (dd or 100), dispose, blend)
            current.from_idat = b"IDAT" not in seen
            p.frames.append(current)
        elif kind == b"fdAT":
            if length < 4:
                p.problems.append("fdAT is too short")
                break
            seq = struct.unpack_from(">I", body)[0]
            if seq != seq_expected:
                p.problems.append(f"sequence number {seq} where {seq_expected} was due")
            seq_expected = seq + 1
            if current is None or current.from_idat:
                p.problems.append("frame data without its frame control")
                break
            current.data.extend(body[4:])
        elif kind in (b"tEXt", b"iTXt") and len(p.texts) < 32:
            key, _, rest = body.partition(b"\0")
            if kind == b"iTXt":
                if len(rest) >= 2 and rest[0] == 0:
                    rest = rest[2:].split(b"\0", 2)[-1]
                else:
                    rest = b""
                text = rest.decode("utf-8", "replace")
            else:
                text = rest.decode("latin-1")
            p.texts[key.decode("latin-1")[:79]] = text[:4096]
        elif kind[0] & 0x20 == 0 and kind not in KNOWN_CRITICAL:
            p.problems.append(f"an unknown critical chunk {kind.decode('latin-1', 'replace')!r}")
    if p.problems or not p.width:
        return p
    if b"IDAT" not in seen:
        p.problems.append("no image data")
        return p
    if p.color == 3 or p.depth not in (8, 16) or p.interlace:
        p.problems.append("palette images, bit depths below 8 and interlaced images are recognised but not read here")
        return p
    channels = CHANNELS[p.color]
    if p.width * p.height * channels * (p.depth // 8) * max(1, len(p.frames)) > limits.max_decoded_bytes:
        p.problems.append("more pixels than the decode limit")
        return p
    try:
        row = p.width * channels * p.depth // 8 + 1
        p.image = _pixels(_inflate(bytes(idat), row * p.height), p.width, p.height, p.depth, p.color,
                          deadline)
        for f in p.frames:
            if f.from_idat:
                if (f.width, f.height, f.x, f.y) != (p.width, p.height, 0, 0):
                    raise PngError("the first frame of the animation must cover the canvas")
                f.pixels = p.image
            else:
                row = f.width * channels * p.depth // 8 + 1
                f.pixels = _pixels(_inflate(bytes(f.data), row * f.height), f.width, f.height, p.depth,
                                   p.color, deadline)
        p.decoded = True
    except PngError as error:
        p.problems.append(str(error))
    return p


def compose(p: Png) -> list[np.ndarray]:
    """The canvas after each frame of an animation (APNG dispose and blend operations)."""
    top = (1 << p.depth) - 1
    channels = CHANNELS[p.color]
    alpha = channels in (2, 4)
    canvas = np.zeros((p.height, p.width, channels), dtype=np.float64)
    out = []
    for f in p.frames:
        region = (slice(f.y, f.y + f.height), slice(f.x, f.x + f.width))
        before = canvas[region].copy()
        src = f.pixels.astype(np.float64)
        if f.blend == 1 and alpha:
            a = src[:, :, -1:] / top
            b = canvas[region][:, :, -1:] / top
            out_a = a + b * (1 - a)
            colour = np.where(out_a > 0, (src[:, :, :-1] * a + canvas[region][:, :, :-1] * b * (1 - a))
                              / np.maximum(out_a, 1e-12), 0)
            canvas[region] = np.concatenate([colour, out_a * top], axis=2)
        else:
            canvas[region] = src
        out.append(np.round(canvas).astype(np.int64))
        if f.dispose == 1:
            canvas[region] = 0
        elif f.dispose == 2:
            canvas[region] = before
    return out


# ---------------------------------------------------------------------------------------------------------------
# The reading contract, re-derived from the spec


def _grid(rows) -> np.ndarray:
    return np.array([[np.nan if v is None else float(v) for v in r] for r in rows], dtype=np.float64)


def _contract(spec, source):
    form = getattr(spec, "spec", None)
    if form not in ("image_raster", "image_apng"):
        return None
    if source is not None:
        grids = [np.asarray(g, dtype=np.float64) for g in (source if form == "image_apng" else [source])]
    else:
        grids = [_grid(spec.data["grid"])] if form == "image_raster" else [_grid(g) for g in spec.data["frames"]]
    o = spec.options
    if "range" in o:
        lo, hi = float(o["range"][0]), float(o["range"][1])
    else:
        present = np.concatenate([g[np.isfinite(g)] for g in grids])
        lo, hi = (float(present.min()), float(present.max())) if present.size else (0.0, 1.0)
    delays = None
    if form == "image_apng":
        delays = list(spec.data["delays_ms"])
        delays = delays * len(grids) if len(delays) == 1 else delays
    return {"grids": grids, "lo": lo, "hi": hi, "ramp": o.get("ramp", "dark_low"), "bits": int(o.get("bits", 8)),
            "cell": o.get("cell_px"), "above": o.get("warn_above"), "below": o.get("warn_below"), "delays": delays}


def _recover(picture: np.ndarray, e: dict, grid: np.ndarray, cell: int, top: int):
    """(values recovered within tolerance, missing marked right, hue findings) for one picture."""
    rows, cols = grid.shape
    centre = picture[cell // 2::cell, cell // 2::cell][:rows, :cols]
    channels = picture.shape[2]
    has_alpha = channels in (2, 4)
    colour = centre[:, :, :3] if channels >= 3 else centre[:, :, :1]
    gray_ok = (colour == colour[:, :, :1]).all(axis=2)
    level = colour[:, :, 0].astype(np.float64)
    if e["ramp"] == "light_low":
        level = top - level
    span = e["hi"] - e["lo"]
    value = e["lo"] + level / top * span if span else np.full(level.shape, e["lo"])
    want = np.clip(grid, e["lo"], e["hi"])
    tol = abs(span) / top / 2 + 1e-9 * max(abs(e["lo"]), abs(e["hi"]), 1.0)
    present = np.isfinite(grid)
    hits = present & gray_ok & (np.abs(np.where(present, value - want, 0)) <= tol)
    if not span:
        hits = present & gray_ok & (np.abs(level - top // 2) <= 0.5)
    alpha = centre[:, :, -1] if has_alpha else np.full(grid.shape, top)
    missing_ok = bool(((alpha == 0) == ~present).all())
    # hue: anywhere a pixel's colour channels differ
    flagged = np.zeros(grid.shape, dtype=bool)
    with np.errstate(invalid="ignore"):
        if e["above"] is not None:
            flagged |= grid > float(e["above"])
        if e["below"] is not None:
            flagged |= grid < float(e["below"])
    if channels >= 3:
        hue = ~((picture[:, :, 0] == picture[:, :, 1]) & (picture[:, :, 1] == picture[:, :, 2]))
        if has_alpha:
            hue &= picture[:, :, 3] > 0
        allowed = np.kron(flagged, np.ones((cell, cell), dtype=bool))
        stray = int((hue & ~allowed).sum())
        cells_with_hue = hue.reshape(rows, cell, cols, cell).any(axis=(1, 3))
        unmarked = int((flagged & present & ~cells_with_hue).sum())
    else:
        stray = 0
        unmarked = int((flagged & present).sum())
    return int(hits.sum()), int(present.sum()), missing_ok, stray, unmarked


def check_png(data: bytes, media_type: str = "image/png", spec=None, source=None) -> Verdict:
    p = read(data)
    technical: list[Finding] = []
    style: list[Finding] = []
    structural = [x for x in p.problems if "not read here" not in x]
    technical.append(Finding("png_structure", not structural,
                             "the chunks follow the PNG rules" if not structural else "; ".join(structural[:3])))
    technical.append(Finding("crc", p.crc_errors == 0, f"{p.crc_errors} chunk(s) fail their CRC-32",
                             measure=float(p.crc_errors)))
    technical.append(Finding("pixels_decoded", p.decoded, f"{p.width} by {p.height} pixels, {p.depth} bits, colour "
                                                          f"type {p.color}" if p.decoded else
                             "the pixels were not read: " + ("; ".join(p.problems[:2]) or "unknown")))
    if p.animated:
        ok = p.num_frames == len(p.frames) and p.num_frames > 0
        technical.append(Finding("animation", ok, f"acTL announces {p.num_frames} frame(s); {len(p.frames)} found"))
    description = p.texts.get("Description", "")
    style.append(Finding("description", bool(description.strip()), "the file states what it shows in words"
                         if description.strip() else "no description in the file"))
    e = _contract(spec, source)
    if e is None or not p.decoded:
        if e is None:
            style.append(Finding("values_not_compared", True, "no raster spec was given, so no values were compared"))
        return Verdict.of(technical, style)
    rows, cols = e["grids"][0].shape
    cell = p.width // cols if cols else 0
    square = cell > 0 and p.width == cols * cell and p.height == rows * cell and (e["cell"] in (None, cell))
    if e["cell"] is None and square:
        square = cell == max(1, TARGET_PX // max(rows, cols))
    technical.append(Finding("dimensions", square, f"{p.width} by {p.height} pixels for a grid of {rows} rows and "
                                                   f"{cols} columns"))
    expected_depth = e["bits"]
    technical.append(Finding("bit_depth", p.depth == expected_depth, f"{p.depth} bits; the spec asks for "
                                                                     f"{expected_depth}"))
    if not square:
        return Verdict.of(technical, style)
    if e["delays"] is not None:
        technical.append(Finding("frame_count", len(p.frames) == len(e["grids"]),
                                 f"{len(p.frames)} frame(s); the spec calls for {len(e['grids'])}"))
        got = [round(f.delay_s * 1000, 6) for f in p.frames]
        technical.append(Finding("delays", got == [float(d) for d in e["delays"]],
                                 "every frame lasts as the spec states" if got == [float(d) for d in e["delays"]]
                                 else "frame delays differ from the spec"))
        pictures = compose(p) if p.frames else []
    else:
        pictures = [p.image.astype(np.int64)]
    top = (1 << p.depth) - 1
    hits = total = stray = unmarked = 0
    missing_ok = True
    for picture, grid in zip(pictures, e["grids"], strict=False):
        h, n, m_ok, s, u = _recover(picture, e, grid, cell, top)
        hits, total, stray, unmarked = hits + h, total + n, stray + s, unmarked + u
        missing_ok &= m_ok
    if len(pictures) != len(e["grids"]):
        total = sum(int(np.isfinite(g).sum()) for g in e["grids"])
    share = hits / total if total else 1.0
    technical.append(Finding("values_recovered", hits == total, f"{hits} of {total} values read back through the "
                                                                "stated gray ramp within half a gray step",
                             measure=share))
    technical.append(Finding("missing_marked", missing_ok, "missing values are transparent and only they"
                             if missing_ok else "transparency does not match the missing values"))
    technical.append(Finding("hue_only_on_warnings", stray == 0 and unmarked == 0,
                             f"{stray} pixel(s) with hue outside a flagged cell; {unmarked} flagged cell(s) without an "
                             "outline"))
    return Verdict.of(technical, style)


__all__ = ["Png", "PngError", "check_png", "compose", "read"]
