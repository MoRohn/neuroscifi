"""PNG and animated PNG (APNG), decoded to pixels in Python and numpy.

All colour types (grey, RGB, palette, grey+alpha, RGBA), bit depths 1-16, palette and single-colour transparency
(tRNS), Adam7 interlace, the five filter types, every chunk's CRC and the zlib stream's Adler-32 checked. Text
chunks (tEXt, zTXt, iTXt) are the artifact's own words (untrusted). APNG: every frame composed on the canvas with
its blend and dispose rules, with its delay.

Bounds: width x height x channels x bytes per sample is checked against `max_decoded_bytes` before any pixel buffer
exists; the image data stream is inflated to at most the exact size its header implies (a stream that inflates
past it is refused); compressed text is inflated to at most 1 MiB per chunk. The deflate ratio rule is not applied
to image data, whose output size is fixed by the (already budgeted) header: flat images legitimately compress over
1000:1. Colour management (gAMA, cHRM, sRGB, iCCP) is not applied: pixel values are as stored.
"""
from __future__ import annotations

import struct
import zlib

import numpy as np

from .. import repr_b2 as R
from ..safe_b2 import Deadline, Reader, inflate, members, need, screen_words
from .. import types as T
from ..types import Decoded, Detected, Limits, Malformed
from . import Adapter

SIGNATURE = b"\x89PNG\r\n\x1a\n"
CHANNELS = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}
DEPTHS = {0: (1, 2, 4, 8, 16), 2: (8, 16), 3: (1, 2, 4, 8), 4: (8, 16), 6: (8, 16)}
ADAM7 = ((0, 0, 8, 8), (4, 0, 8, 8), (0, 4, 4, 8), (2, 0, 4, 4), (0, 2, 2, 4), (1, 0, 2, 2), (0, 1, 1, 2))
TEXT_CHUNK_MAX = 1 << 20
WORDS_MAX = 1 << 20
COLOUR_CHUNKS = (b"gAMA", b"cHRM", b"sRGB", b"iCCP")


def unpack_samples(rows: np.ndarray, bits: int, n: int) -> np.ndarray:
    """(h, rowbytes) uint8 of packed big-endian samples -> (h, n) uint8 (bits < 8) or uint16 (bits 16)."""
    if bits == 8:
        return rows[:, :n]
    if bits == 16:
        return rows[:, :2 * n].reshape(rows.shape[0], n, 2).astype(np.uint16) @ np.array([256, 1], np.uint16)
    per = 8 // bits
    shifts = np.arange(8 - bits, -1, -bits, dtype=np.uint8)
    out = (rows[:, :, None] >> shifts) & np.uint8((1 << bits) - 1)
    return out.reshape(rows.shape[0], rows.shape[1] * per)[:, :n]


def _paeth(a, b, c):
    p = a + b - c
    pa, pb, pc = np.abs(p - a), np.abs(p - b), np.abs(p - c)
    return np.where((pa <= pb) & (pa <= pc), a, np.where(pb <= pc, b, c))


def unfilter(raw: bytes, h: int, rowbytes: int, bpp: int, deadline: Deadline) -> np.ndarray:
    """Undo PNG row filters. `raw` holds h rows of (1 + rowbytes) bytes. Returns (h, rowbytes) uint8."""
    if h == 0 or rowbytes == 0:
        return np.zeros((h, rowbytes), np.uint8)
    arr = np.frombuffer(raw, np.uint8).reshape(h, rowbytes + 1)
    ft = arr[:, 0].astype(np.int64)
    if (ft > 4).any():
        raise Malformed("a PNG row uses an unknown filter type")
    filt = arr[:, 1:]
    if not np.isin(ft, (3, 4)).any():       # None/Sub/Up only: row by row, vectorised
        out = np.empty((h, rowbytes), np.uint8)
        prev = np.zeros(rowbytes, np.uint8)
        for y in range(h):
            row = filt[y]
            if ft[y] == 0:
                cur = row.copy()
            elif ft[y] == 2:
                cur = row + prev
            else:
                cur = np.cumsum(row.reshape(-1, bpp), axis=0, dtype=np.uint8).reshape(-1)
            out[y] = cur
            prev = cur
            if y % 256 == 0:
                deadline.check("PNG unfiltering")
        return out
    # Average/Paeth present: reconstruct along anti-diagonals (each needs its left, upper and upper-left neighbour),
    # all pixels of one diagonal at once.
    npix = rowbytes // bpp
    f = filt.reshape(h, npix, bpp).astype(np.int32)
    rec = np.zeros((h + 1, npix + 1, bpp), np.int32)
    for d in range(h + npix - 1):
        y = np.arange(max(0, d - npix + 1), min(h - 1, d) + 1)
        x = d - y
        a, b, c = rec[y + 1, x], rec[y, x + 1], rec[y, x]
        t = ft[y][:, None]
        pred = np.where(t == 1, a, np.where(t == 2, b, np.where(t == 3, (a + b) >> 1,
                                                                  np.where(t == 4, _paeth(a, b, c), 0))))
        rec[y + 1, x + 1] = (f[y, x] + pred) & 0xFF
        if d % 512 == 0:
            deadline.check("PNG unfiltering")
    return rec[1:, 1:].reshape(h, rowbytes).astype(np.uint8)


def _rowbytes(w: int, bits: int, ch: int) -> int:
    return (w * bits * ch + 7) // 8


def _decode_image(raw_stream: bytes, w: int, h: int, bits: int, ctype: int, interlace: int,
                  deadline: Deadline) -> np.ndarray:
    """Inflated, unfiltered, unpacked samples (h, w, ch) of one image (or APNG frame)."""
    ch = CHANNELS[ctype]
    bpp = max(1, bits * ch // 8)
    if interlace == 0:
        expect = h * (1 + _rowbytes(w, bits, ch))
        passes = [(0, 0, 1, 1, w, h)]
    else:
        passes = []
        expect = 0
        for x0, y0, dx, dy in ADAM7:
            pw, ph = (w - x0 + dx - 1) // dx if w > x0 else 0, (h - y0 + dy - 1) // dy if h > y0 else 0
            passes.append((x0, y0, dx, dy, pw, ph))
            if pw and ph:
                expect += ph * (1 + _rowbytes(pw, bits, ch))
    data = inflate(raw_stream, expect, what="the PNG image data", deadline=deadline, complete=True)
    if len(data) != expect:
        raise Malformed(f"the PNG image data holds {len(data)} bytes, its header implies {expect}")
    dtype = np.uint16 if bits == 16 else np.uint8
    out = np.zeros((h, w, ch), dtype)
    at = 0
    for x0, y0, dx, dy, pw, ph in passes:
        if not (pw and ph):
            continue
        rb = _rowbytes(pw, bits, ch)
        size = ph * (1 + rb)
        rows = unfilter(data[at:at + size], ph, rb, bpp, deadline)
        at += size
        samples = unpack_samples(rows, bits, pw * ch).reshape(ph, pw, ch)
        out[y0::dy, x0::dx] = samples
    return out


def _to_raster_array(samples: np.ndarray, ctype: int, bits: int, palette, trns):
    """(pixels, mode, bits) in a Raster mode from raw samples."""
    h, w, _ = samples.shape
    if ctype == 3:
        if palette is None:
            raise Malformed("a palette PNG has no PLTE chunk")
        idx = samples[:, :, 0]
        if idx.size and int(idx.max()) >= len(palette):
            raise Malformed("a PNG pixel refers to a palette entry that does not exist")
        if trns is not None:
            alpha = np.full(len(palette), 255, np.uint8)
            alpha[:min(len(trns), len(palette))] = np.frombuffer(trns, np.uint8)[:len(palette)]
            table = np.concatenate([palette, alpha[:, None]], axis=1)
            return table[idx], "rgba", 8
        return palette[idx], "rgb", 8
    if ctype == 0:
        gray = samples[:, :, 0]
        if trns is not None and len(trns) >= 2:
            key = struct.unpack(">H", trns[:2])[0]
            top = (1 << bits) - 1
            alpha = np.where(gray == key, 0, top).astype(gray.dtype)
            return np.stack([gray, gray, gray, alpha], axis=2), "rgba", bits
        return gray.copy(), "gray", bits
    if ctype == 2:
        if trns is not None and len(trns) >= 6:
            key = np.array(struct.unpack(">HHH", trns[:6]), dtype=samples.dtype)
            top = (1 << bits) - 1
            alpha = np.where((samples == key).all(axis=2), 0, top).astype(samples.dtype)
            return np.concatenate([samples, alpha[:, :, None]], axis=2), "rgba", bits
        return samples, "rgb", bits
    if ctype == 4:
        g, a = samples[:, :, 0], samples[:, :, 1]
        return np.stack([g, g, g, a], axis=2), "rgba", bits
    return samples, "rgba", bits


def _text_chunk(ctype: bytes, data: bytes) -> str:
    key, sep, rest = data.partition(b"\x00")
    if not sep or not 1 <= len(key) <= 79:
        raise Malformed("a PNG text chunk has no valid keyword")
    keyword = key.decode("latin-1")
    if ctype == b"tEXt":
        return f"{keyword}: {rest.decode('latin-1')}"
    if ctype == b"zTXt":
        if not rest or rest[0] != 0:
            raise Malformed("a zTXt chunk names an unknown compression method")
        return f"{keyword}: {inflate(rest[1:], TEXT_CHUNK_MAX, what='a zTXt chunk').decode('latin-1')}"
    if len(rest) < 2:
        raise Malformed("an iTXt chunk is too short")
    flag, method, tail = rest[0], rest[1], rest[2:]
    _lang, _, tail = tail.partition(b"\x00")
    _translated, _, text = tail.partition(b"\x00")
    if flag:
        if method != 0:
            raise Malformed("an iTXt chunk names an unknown compression method")
        text = inflate(text, TEXT_CHUNK_MAX, what="an iTXt chunk")
    return f"{keyword}: {text.decode('utf-8', 'replace')}"


def _chunks(data: bytes, deadline: Deadline):
    r = Reader(data, ">", 8, "the PNG file")
    n = 0
    while r.remaining:
        length = r.u32()
        if length > 0x7FFFFFFF:
            raise Malformed("a PNG chunk declares a length over 2^31")
        ctype = r.take(4)
        body = r.take(length)
        crc = r.u32()
        if zlib.crc32(ctype + body) != crc:
            raise Malformed(f"the PNG chunk {ctype.decode('latin-1')!r} fails its CRC check")
        yield ctype, body
        n += 1
        if n % 1024 == 0:
            deadline.check("reading PNG chunks")
        if ctype == b"IEND":
            return
    raise Malformed("the PNG file has no IEND chunk")


def _over(dst: np.ndarray, src: np.ndarray, top: int) -> np.ndarray:
    """APNG blend_op OVER on RGBA arrays (float math, rounded back)."""
    sa = src[..., 3:4].astype(np.float64) / top
    da = dst[..., 3:4].astype(np.float64) / top
    oa = sa + da * (1 - sa)
    with np.errstate(invalid="ignore", divide="ignore"):
        rgb = (src[..., :3] * sa + dst[..., :3] * da * (1 - sa)) / np.where(oa == 0, 1, oa)
    out = np.concatenate([np.where(oa == 0, 0, rgb), oa * top], axis=-1)
    return np.clip(np.rint(out), 0, top).astype(dst.dtype)


def _to_rgba(arr: np.ndarray, mode: str, bits: int):
    """Any Raster array -> RGBA of 8 or 16 bits (low bit depths scaled to 8)."""
    target = 16 if bits == 16 else 8
    top = (1 << target) - 1
    if bits not in (8, 16):
        arr = (arr.astype(np.uint16) * 255 // ((1 << bits) - 1)).astype(np.uint8)
    if mode == "gray":
        arr = np.stack([arr, arr, arr, np.full_like(arr, top)], axis=2)
    elif mode == "rgb":
        arr = np.concatenate([arr, np.full(arr.shape[:2] + (1,), top, arr.dtype)], axis=2)
    return arr, target


class PNGAdapter(Adapter):
    name = "b2-png"
    formats = ("png",)
    families = ("image",)

    def decode(self, data: bytes, detected: Detected, limits: Limits) -> Decoded:
        deadline = Deadline(limits)
        if data[:8] != SIGNATURE:
            raise Malformed("the PNG signature is missing")
        notes: list[str] = []
        words: list[str] = []
        words_len = 0
        ihdr = palette = trns = None
        idat: list[bytes] = []
        actl = None
        frames: list[dict] = []       # APNG: {"fctl": ..., "data": [bytes]}
        seen_idat = False
        colour = set()
        for ctype, body in _chunks(data, deadline):
            if ctype == b"IHDR":
                if ihdr is not None or len(body) != 13:
                    raise Malformed("the PNG IHDR chunk is repeated or has the wrong length")
                ihdr = struct.unpack(">IIBBBBB", body)
                continue
            if ihdr is None:
                raise Malformed("the first PNG chunk is not IHDR")
            if ctype == b"PLTE":
                if len(body) % 3 or not 1 <= len(body) // 3 <= 256:
                    raise Malformed("the PNG palette has an invalid length")
                palette = np.frombuffer(body, np.uint8).reshape(-1, 3)
            elif ctype == b"tRNS":
                trns = body
            elif ctype == b"IDAT":
                seen_idat = True
                idat.append(body)
            elif ctype == b"acTL":
                if len(body) != 8:
                    raise Malformed("the APNG acTL chunk has the wrong length")
                actl = struct.unpack(">II", body)
                members(actl[0], limits, "APNG frames")
            elif ctype == b"fcTL":
                if len(body) != 26:
                    raise Malformed("an APNG fcTL chunk has the wrong length")
                frames.append({"fctl": struct.unpack(">IIIIIHHBB", body), "data": [], "idat": not seen_idat})
                members(len(frames), limits, "APNG frames")
            elif ctype == b"fdAT":
                if not frames or len(body) < 4:
                    raise Malformed("an APNG fdAT chunk comes before any fcTL")
                frames[-1]["data"].append(body[4:])
            elif ctype in (b"tEXt", b"zTXt", b"iTXt"):
                if words_len < WORDS_MAX:
                    try:
                        text = _text_chunk(ctype, body)
                        words.append(text)
                        words_len += len(text)
                    except Malformed:
                        notes.append(f"a malformed {ctype.decode()} chunk was skipped")
            elif ctype in COLOUR_CHUNKS:
                colour.add(ctype.decode())
            elif ctype == b"eXIf":
                notes.append("an eXIf (Exif) chunk is present; its fields are not decoded")
        if ihdr is None or not idat:
            raise Malformed("the PNG file has no IHDR or no image data")
        w, h, bits, ctype, comp, filt, interlace = ihdr
        if not (1 <= w <= 2 ** 31 - 1 and 1 <= h <= 2 ** 31 - 1):
            raise Malformed("the PNG dimensions are out of range")
        if ctype not in CHANNELS or bits not in DEPTHS[ctype] or comp != 0 or filt != 0 or interlace > 1:
            raise Malformed("the PNG header names an invalid combination of colour type, depth or method")
        out_ch = 4 if (ctype in (4, 6) or trns is not None) else (3 if ctype in (2, 3) else 1)
        bytes_per = 2 if bits == 16 else 1
        need(w * h * out_ch * bytes_per * 2, limits, "the PNG image")
        if colour:
            notes.append("colour management chunks (" + ", ".join(sorted(colour)) + ") are not applied: "
                         "pixel values are as stored")
        if interlace:
            notes.append("Adam7-interlaced image: all seven passes decoded")
        if ctype == 4:
            notes.append("grey+alpha image represented as RGBA with the grey value in each colour channel")
        if ctype == 0 and trns is not None:
            notes.append("grey image with a transparent key value represented as RGBA")
        text = "\n".join(words)
        notes.extend(screen_words(text))
        samples = _decode_image(b"".join(idat), w, h, bits, ctype, interlace, deadline)
        pixels, mode, out_bits = _to_raster_array(samples, ctype, bits, palette, trns)
        if not frames or actl is None:
            if frames or actl is not None:
                notes.append("APNG control chunks are incomplete: only the default image was decoded")
            return Decoded(detected, R.Raster(np.ascontiguousarray(pixels), mode, out_bits, words=text),
                           tuple(notes), False)
        return self._animate(detected, frames, actl, pixels, mode, out_bits, ihdr, palette, trns, text, notes,
                             frames[0]["idat"], limits, deadline)

    def _animate(self, detected, frames, actl, first, mode, bits, ihdr, palette, trns, text, notes, idat_first,
                 limits, deadline):
        w, h, depth, ctype, _, _, _ = ihdr
        canvas_rgba, target = _to_rgba(first, mode, bits)
        top = (1 << target) - 1
        need(w * h * 4 * (target // 8) * (len(frames) + 2), limits, "the APNG frames")
        if depth not in (8, 16):
            notes.append(f"APNG frames of {depth}-bit samples were scaled to 8 bits to compose them")
        canvas = np.zeros((h, w, 4), canvas_rgba.dtype)
        rasters, delays = [], []
        for i, frame in enumerate(frames):
            deadline.check("composing APNG frames")
            _, fw, fh, fx, fy, dnum, dden, dispose, blend = frame["fctl"]
            if fw == 0 or fh == 0 or fx + fw > w or fy + fh > h or dispose > 2 or blend > 1:
                raise Malformed(f"APNG frame {i} lies outside the canvas or names unknown operations")
            if i == 0 and frame["idat"]:
                if (fw, fh, fx, fy) != (w, h, 0, 0):
                    raise Malformed("the first APNG frame must cover the canvas")
                sub = canvas_rgba
            else:
                if not frame["data"]:
                    raise Malformed(f"APNG frame {i} has no image data")
                samples = _decode_image(b"".join(frame["data"]), fw, fh, depth, ctype, ihdr[6], deadline)
                arr, m, b = _to_raster_array(samples, ctype, depth, palette, trns)
                sub, _ = _to_rgba(arr, m, b)
            if i == 0 and dispose == 2:
                dispose = 1
            before = canvas[fy:fy + fh, fx:fx + fw].copy()
            if blend == 0:
                canvas[fy:fy + fh, fx:fx + fw] = sub
            else:
                canvas[fy:fy + fh, fx:fx + fw] = _over(before, sub, top)
            rasters.append(T.Raster(canvas.copy(), "rgba", target))
            delays.append(dnum / (dden or 100))
            if dispose == 1:
                canvas[fy:fy + fh, fx:fx + fw] = 0
            elif dispose == 2:
                canvas[fy:fy + fh, fx:fx + fw] = before
        if not idat_first:
            notes.append("the default image is not part of the animation and is not among the frames")
        if actl[0] != len(frames):
            notes.append(f"the animation declares {actl[0]} frames and holds {len(frames)}")
        notes.append(f"animation plays {actl[1] or 'endlessly'} time(s); frames are the composed canvas")
        return Decoded(detected, R.Frames(tuple(rasters), tuple(delays), words=text), tuple(notes), False)


ADAPTERS = (PNGAdapter(),)

__all__ = ["ADAPTERS", "PNGAdapter", "unfilter", "unpack_samples"]
