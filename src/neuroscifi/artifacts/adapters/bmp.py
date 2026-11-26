"""BMP (Windows and OS/2 bitmaps) and ICO/CUR icons, decoded to pixels.

BMP: header sizes 12 (OS/2 core), 40 (INFO), 52/56 (with masks), 108 (V4), 124 (V5); 1/4/8-bit palette images,
16-bit (5-5-5 by default, or bit-field masks), 24-bit BGR, 32-bit BGRx or with masks (an alpha mask makes RGBA;
a 32-bit image whose alpha bytes are all zero is opaque, as viewers show it); bottom-up and top-down row order;
RLE8 and RLE4 compression ARE decoded (absolute runs, end of line, delta, end of bitmap; pixels the delta skips
are transparent); a PNG held in a BMP is decoded by the PNG adapter; a JPEG held in a BMP is refused with
its header facts. ICO/CUR: the directory is read and the largest image (PNG or a headerless BMP with its AND mask)
is decoded; the other entries are listed in the notes.
"""
from __future__ import annotations

import struct

import numpy as np

from ..safe import Deadline
from ..safe_b2 import Reader, members, need
from ..types import Decoded, Detected, HeaderOnly, Limits, Malformed, Raster
from . import Adapter
from .png import PNGAdapter, unpack_samples

COMPRESSION = {0: "none", 1: "RLE8", 2: "RLE4", 3: "bit fields", 4: "JPEG", 5: "PNG", 6: "alpha bit fields"}


def _mask_channel(px: np.ndarray, mask: int) -> tuple[np.ndarray, int]:
    """Extract a bit-field channel scaled to 8 bits."""
    if mask == 0:
        return np.zeros(px.shape, np.uint8), 0
    shift = (mask & -mask).bit_length() - 1
    width = (mask >> shift).bit_length()
    v = (px & mask) >> shift
    top = (1 << width) - 1
    return ((v.astype(np.uint64) * 255 + top // 2) // top).astype(np.uint8), width


def _rle(data: bytes, w: int, h: int, four: bool, deadline: Deadline) -> tuple[np.ndarray, np.ndarray]:
    """RLE8/RLE4 -> (indices (h, w) bottom-up, written mask)."""
    idx = np.zeros((h, w), np.uint8)
    written = np.zeros((h, w), bool)
    x = y = 0
    r = Reader(data, "<", 0, "the RLE stream")
    steps = 0
    while r.remaining >= 2:
        steps += 1
        if steps % 65536 == 0:
            deadline.check("decoding BMP RLE")
        n, c = r.u8(), r.u8()
        if n:
            if y < h and x < w:
                k = min(n, w - x)
                if four:
                    idx[y, x:x + k] = np.where(np.arange(k) % 2 == 0, c >> 4, c & 0xF)
                else:
                    idx[y, x:x + k] = c
                written[y, x:x + k] = True
            x += n
        elif c == 0:
            x, y = 0, y + 1
        elif c == 1:
            break
        elif c == 2:
            dx, dy = r.u8(), r.u8()
            x, y = x + dx, y + dy
        else:
            nbytes = (c + 1) // 2 if four else c
            raw = r.take(nbytes)
            if nbytes % 2:
                r.skip(1)
            vals = np.frombuffer(raw, np.uint8)
            if four:
                vals = np.stack([vals >> 4, vals & 0xF], axis=1).reshape(-1)[:c]
            if y < h and x < w:
                k = min(c, w - x)
                idx[y, x:x + k] = vals[:k]
                written[y, x:x + k] = True
            x += c
        if y > h:
            break
    return idx, written


def decode_dib(data: bytes, at: int, pixel_at: int | None, detected: Detected, limits: Limits, *,
               icon: bool = False, notes: list | None = None) -> Raster:
    """A device-independent bitmap whose header starts at `at`. `pixel_at` None: pixels follow the palette.
    `icon`: the height is doubled and an AND mask follows the colour bitmap."""
    deadline = Deadline(limits.max_seconds)
    notes = [] if notes is None else notes
    r = Reader(data, "<", at, "the BMP header")
    hsize = r.u32()
    if hsize == 12:
        w, h, planes, bpp = r.unpack("HHHH")
        comp, colours, masks = 0, 0, None
    elif hsize in (16, 40, 52, 56, 64, 108, 124):
        w, h, planes, bpp = r.unpack("iiHH")
        comp = r.u32() if hsize >= 20 else 0
        colours = Reader(data, "<", at + 32, "the BMP header").u32() if hsize >= 36 else 0
        masks = None
        if comp in (3, 6):
            mr = Reader(data, "<", at + 40, "the BMP masks")
            masks = mr.unpack("IIII") if (hsize >= 56 or comp == 6) else mr.unpack("III") + (0,)
    else:
        raise Malformed(f"BMP header size {hsize} is unknown")
    top_down = h < 0
    h = abs(h)
    if icon:
        h //= 2
    if not (0 < w <= 1 << 16 and 0 < h <= 1 << 16) or bpp not in (1, 2, 4, 8, 16, 24, 32):
        raise Malformed("the BMP size or bit count is out of range")
    need(w * h * 4 * 4, limits, "the BMP image")
    if comp == 4:
        raise HeaderOnly(f"a BMP holding a JPEG ({w}x{h}) is not decoded here",
                         facts={"format": "bmp", "width": w, "height": h, "compression": "JPEG"},
                         needs=("a JPEG decoder",), size=len(data))
    if comp == 5:
        start = pixel_at if pixel_at is not None else at + hsize
        inner = PNGAdapter().decode(data[start:], detected, limits)
        notes.append("a PNG held in the BMP was decoded")
        return inner.representation
    if comp not in (0, 1, 2, 3, 6):
        raise Malformed(f"BMP compression {comp} is unknown")
    palette = None
    pal_at = at + hsize + (12 if comp == 3 and hsize == 40 else 16 if comp == 6 and hsize == 40 else 0)
    if bpp <= 8:
        n = colours or (1 << bpp)
        if n > 256:
            raise Malformed("the BMP palette has more than 256 entries")
        entry = 3 if hsize == 12 else 4
        pal = Reader(data, "<", pal_at, "the BMP palette").take(n * entry)
        palette = np.frombuffer(pal, np.uint8).reshape(n, entry)[:, 2::-1].copy()
        pal_at += n * entry
    start = pixel_at if pixel_at is not None else pal_at
    if comp in (1, 2):
        if top_down or (comp == 1 and bpp != 8) or (comp == 2 and bpp != 4):
            raise Malformed("BMP RLE needs a bottom-up 8- or 4-bit image")
        idx, written = _rle(data[start:], w, h, comp == 2, deadline)
        if int(idx.max(initial=0)) >= len(palette):
            raise Malformed("a BMP pixel refers to a palette entry that does not exist")
        rgb = palette[idx][::-1]
        notes.append(f"{COMPRESSION[comp]} compression decoded")
        if not written.all():
            notes.append("pixels skipped by RLE deltas or line ends are transparent")
            alpha = np.where(written[::-1], 255, 0).astype(np.uint8)
            return Raster(np.concatenate([rgb, alpha[:, :, None]], axis=2), "rgba", 8)
        return Raster(np.ascontiguousarray(rgb), "rgb", 8)
    rowbytes = ((w * bpp + 31) // 32) * 4
    raw = data[start:start + rowbytes * h]
    if len(raw) < rowbytes * h:
        raise Malformed(f"the BMP pixel data is shorter than {h} rows of {rowbytes} bytes")
    rows = np.frombuffer(raw, np.uint8).reshape(h, rowbytes)
    if not top_down:
        rows = rows[::-1]
    alpha = None
    if bpp <= 8:
        idx = unpack_samples(rows, bpp, w)
        if int(idx.max(initial=0)) >= len(palette):
            raise Malformed("a BMP pixel refers to a palette entry that does not exist")
        rgb = palette[idx]
    elif bpp == 24:
        rgb = rows[:, :w * 3].reshape(h, w, 3)[:, :, ::-1]
    else:
        width = bpp // 8
        px = rows[:, :w * width].copy().view("<u2" if bpp == 16 else "<u4").reshape(h, w).astype(np.uint32)
        if masks is None:
            masks = (0x7C00, 0x03E0, 0x001F, 0) if bpp == 16 else (0xFF0000, 0x00FF00, 0x0000FF, 0)
        chans = [_mask_channel(px, m) for m in masks[:3]]
        rgb = np.stack([c[0] for c in chans], axis=2)
        if masks[3]:
            alpha = _mask_channel(px, masks[3])[0]
        elif bpp == 32 and icon:
            alpha = ((px >> 24) & 0xFF).astype(np.uint8)
    if icon and alpha is None:
        and_rowbytes = ((w + 31) // 32) * 4
        and_at = start + rowbytes * h
        mask_raw = data[and_at:and_at + and_rowbytes * h]
        if len(mask_raw) == and_rowbytes * h:
            m = np.unpackbits(np.frombuffer(mask_raw, np.uint8).reshape(h, and_rowbytes), axis=1)[:, :w]
            alpha = np.where(m[::-1] if not top_down else m, 0, 255).astype(np.uint8)
        else:
            notes.append("the icon's AND mask is missing; the image is opaque")
    if alpha is not None and not (bpp == 32 and not icon and not alpha.any()):
        return Raster(np.ascontiguousarray(np.concatenate([rgb, alpha[:, :, None]], axis=2)), "rgba", 8)
    if alpha is not None:
        notes.append("the alpha channel is zero everywhere: the image is treated as opaque")
    return Raster(np.ascontiguousarray(rgb), "rgb", 8)


def decode_bmp(data: bytes, detected: Detected, limits: Limits) -> Decoded:
    if data[:2] != b"BM" or len(data) < 26:
        raise Malformed("the BMP signature is missing")
    offset = struct.unpack("<I", data[10:14])[0]
    if not 14 < offset <= len(data):
        raise Malformed("the BMP pixel offset points outside the file")
    notes: list[str] = []
    raster = decode_dib(data, 14, offset, detected, limits, notes=notes)
    return Decoded(detected, raster, tuple(notes), False)


def decode_ico(data: bytes, detected: Detected, limits: Limits) -> Decoded:
    r = Reader(data, "<", 0, "the icon directory")
    _, kind, n = r.unpack("HHH")
    members(n, limits, "icon images")
    entries = []
    for i in range(n):
        w, h, colours, _, planes, bpp, size, offset = r.unpack("BBBBHHII")
        entries.append({"i": i, "w": w or 256, "h": h or 256, "bpp": bpp, "size": size, "offset": offset})
    if not entries:
        raise Malformed("the icon directory is empty")
    best = max(entries, key=lambda e: (e["w"] * e["h"], e["bpp"]))
    if best["offset"] + best["size"] > len(data):
        raise Malformed("an icon image lies outside the file")
    blob = data[best["offset"]:best["offset"] + best["size"]]
    notes = [f"{'cursor' if kind == 2 else 'icon'} with {n} image(s): " +
             ", ".join(f"{e['w']}x{e['h']}" for e in entries[:16]) + f"; the largest ({best['w']}x{best['h']}) decoded"]
    if blob[:8] == b"\x89PNG\r\n\x1a\n":
        inner = PNGAdapter().decode(blob, detected, limits)
        return Decoded(detected, inner.representation, tuple(notes) + inner.notes, False)
    raster = decode_dib(blob, 0, None, detected, limits, icon=True, notes=notes)
    return Decoded(detected, raster, tuple(notes), False)


class BMPAdapter(Adapter):
    name = "b2-bmp"
    formats = ("bmp",)
    families = ("image",)

    def decode(self, data, detected, limits):
        return decode_bmp(data, detected, limits)


class ICOAdapter(Adapter):
    name = "b2-ico"
    formats = ("ico",)
    families = ("image",)

    def decode(self, data, detected, limits):
        return decode_ico(data, detected, limits)


ADAPTERS = (BMPAdapter(), ICOAdapter())

__all__ = ["ADAPTERS", "BMPAdapter", "ICOAdapter", "decode_bmp", "decode_dib", "decode_ico"]
