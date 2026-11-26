"""GIF (87a and 89a) decoded to pixels: an LZW decoder in Python, global and local colour tables, interlace,
transparency, and every frame composed on the logical screen with its delay and disposal method.

One frame: a `Raster` (RGB, or RGBA when the frame has a transparent index). Several frames: `Frames` of the
composed canvas (RGB when no pixel of any composed frame is transparent, else RGBA), with each delay as stored (0 s is kept as 0; browsers often show such frames for 0.1 s).
Disposal 2 restores the frame's area to transparent (as browsers do, not to the background colour); disposal 3
restores what was there before. Comment and plain-text extensions are the artifact's own words; the NETSCAPE loop
count is noted. A pixel index beyond the colour table is shown black and counted in the notes.

Bounds: canvas x frames x 4 bytes is checked against `max_decoded_bytes` as frames are added; frames against
`max_members`; each frame's LZW output is bounded by its width x height.
"""
from __future__ import annotations

import numpy as np

from .. import repr_b2 as R
from ..safe import Deadline, injection_notes
from ..safe_b2 import Reader, members, need
from ..types import Decoded, Detected, Limits, Malformed, Raster
from . import Adapter

GIF_FRAME_TEMP = 8          # bytes per frame pixel of temporaries (codes, indices, colour lookup)


def lzw_decode(data: bytes, min_size: int, max_out: int) -> bytes:
    """GIF LZW (variable code width, LSB first, clear and end codes) -> at most `max_out` index bytes."""
    if not 2 <= min_size <= 11:
        raise Malformed("the GIF LZW minimum code size is out of range")
    clear, end = 1 << min_size, (1 << min_size) + 1
    table: list[bytes] = [bytes((i,)) for i in range(clear)] + [b"", b""]
    width = min_size + 1
    out = bytearray()
    acc = nbits = 0
    prev = None
    for byte in data:
        acc |= byte << nbits
        nbits += 8
        while nbits >= width:
            code = acc & ((1 << width) - 1)
            acc >>= width
            nbits -= width
            if code == clear:
                del table[clear + 2:]
                width = min_size + 1
                prev = None
                continue
            if code == end:
                return bytes(out)
            if prev is None:
                if code >= len(table) or code >= clear:
                    raise Malformed("the GIF LZW stream starts with an invalid code")
                entry = table[code]
            elif code < len(table):
                entry = table[code]
                if len(table) < 4096:
                    table.append(prev + entry[:1])
            elif code == len(table):
                entry = prev + prev[:1]
                if len(table) < 4096:
                    table.append(entry)
            else:
                raise Malformed("the GIF LZW stream refers to a code not yet defined")
            out += entry
            if len(out) > max_out:
                return bytes(out[:max_out])
            prev = entry
            if len(table) == (1 << width) and width < 12:
                width += 1
    return bytes(out)


def _sub_blocks(r: Reader) -> bytes:
    parts = []
    while True:
        n = r.u8()
        if n == 0:
            return b"".join(parts)
        parts.append(r.take(n))


def _deinterlace(idx: np.ndarray) -> np.ndarray:
    h = idx.shape[0]
    order = list(range(0, h, 8)) + list(range(4, h, 8)) + list(range(2, h, 4)) + list(range(1, h, 2))
    out = np.empty_like(idx)
    out[order] = idx
    return out


def decode_gif(data: bytes, detected: Detected, limits: Limits) -> Decoded:
    deadline = Deadline(limits.max_seconds)
    r = Reader(data, "<", 0, "the GIF file")
    if r.take(6) not in (b"GIF87a", b"GIF89a"):
        raise Malformed("the GIF signature is missing")
    sw, sh, packed, _bg, _aspect = r.unpack("HHBBB")
    if not (sw and sh):
        raise Malformed("the GIF logical screen has no size")
    gct = None
    if packed & 0x80:
        gct = np.frombuffer(r.take(3 * (2 << (packed & 7))), np.uint8).reshape(-1, 3)
    canvas = np.zeros((sh, sw, 4), np.uint8)
    frames: list[Raster] = []
    delays: list[float] = []
    words: list[str] = []
    notes: list[str] = []
    gce = None
    any_transparent = False
    bad_index = 0
    loops = None
    while True:
        deadline.check("decoding GIF frames")
        if not r.remaining:
            notes.append("the GIF file ends without its trailer")
            break
        block = r.u8()
        if block == 0x3B:
            break
        if block == 0x21:
            label = r.u8()
            body = _sub_blocks(r)
            if label == 0xF9 and len(body) >= 4:
                flags, delay, tindex = body[0], int.from_bytes(body[1:3], "little"), body[3]
                gce = {"dispose": (flags >> 2) & 7, "delay": delay / 100.0,
                       "transparent": tindex if flags & 1 else None}
            elif label == 0xFE:
                words.append(body.decode("latin-1", "replace"))
            elif label == 0x01 and len(body) > 12:
                words.append(body[12:].decode("latin-1", "replace"))
            elif label == 0xFF and body[:11] in (b"NETSCAPE2.0", b"ANIMEXTS1.0") and len(body) >= 14 and body[11] == 1:
                loops = int.from_bytes(body[12:14], "little")
            continue
        if block != 0x2C:
            raise Malformed(f"unknown GIF block {block:#04x}")
        x, y, w, h, fpacked = r.unpack("HHHHB")
        lct = None
        if fpacked & 0x80:
            lct = np.frombuffer(r.take(3 * (2 << (fpacked & 7))), np.uint8).reshape(-1, 3)
        table = lct if lct is not None else gct
        if table is None:
            raise Malformed("a GIF frame has no colour table")
        min_size = r.u8()
        members(len(frames) + 1, limits, "GIF frames")
        need(sw * sh * 4 * (len(frames) + 2) + w * h * GIF_FRAME_TEMP, limits, "the GIF frames")
        idx = np.frombuffer(lzw_decode(_sub_blocks(r), min_size, w * h), np.uint8)
        complete = len(idx) == w * h
        if not complete:
            notes.append(f"frame {len(frames)} holds {len(idx)} of {w * h} pixels; the rest are transparent")
            full = np.zeros(w * h, np.uint8)
            full[:len(idx)] = idx
            present = np.zeros(w * h, bool)
            present[:len(idx)] = True
            full, present = full.reshape(h, w), present.reshape(h, w)
        else:
            full, present = idx.reshape(h, w), None
        if fpacked & 0x40:
            full = _deinterlace(full)
            present = _deinterlace(present) if present is not None else None
        if full.size and int(full.max()) >= len(table):
            over = full >= len(table)
            bad_index += int(over.sum())
            table = np.concatenate([table, np.zeros((256 - len(table), 3), np.uint8)])   # beyond: black
        visible = present
        transparent = gce["transparent"] if gce else None
        if transparent is not None:
            keep = full != transparent
            visible = keep if visible is None else (visible & keep)
            any_transparent = True
        if present is not None:
            any_transparent = True
        dispose = gce["dispose"] if gce else 0
        x1, y1 = min(sw, x + w), min(sh, y + h)
        vw, vh = max(0, x1 - x), max(0, y1 - y)
        before = canvas.copy() if dispose == 3 else None
        if vw and vh:
            area = canvas[y:y1, x:x1]
            sub = full[:vh, :vw]
            if visible is None:
                area[:, :, :3] = table[sub]
                area[:, :, 3] = 255
            else:
                m = visible[:vh, :vw]
                area[m, :3] = table[sub[m]]
                area[m, 3] = 255
        if (x1 - x, y1 - y) != (w, h):
            notes.append(f"frame {len(frames)} extends past the logical screen; the outside part is cut")
        frames.append(Raster(canvas.copy(), "rgba", 8))
        delays.append(gce["delay"] if gce else 0.0)
        if dispose == 2 and vw and vh:
            canvas[y:y1, x:x1] = 0
        elif dispose == 3:
            canvas = before
        gce = None
    if not frames:
        raise Malformed("the GIF file holds no image")
    if bad_index:
        notes.append(f"{bad_index} pixel(s) refer to colours beyond the colour table and are shown black")
    text = "\n".join(w for w in words if w)
    notes.extend(injection_notes(text))
    if len(frames) == 1:
        px = frames[0].pixels
        if any_transparent:
            return Decoded(detected, R.Raster(px, "rgba", 8, words=text), tuple(notes), False)
        return Decoded(detected, R.Raster(np.ascontiguousarray(px[:, :, :3]), "rgb", 8, words=text), tuple(notes),
                       False)
    if loops is not None:
        notes.append(f"animation loops {'endlessly' if loops == 0 else f'{loops} time(s)'}")
    if all(int(f.pixels[:, :, 3].min()) == 255 for f in frames):
        # as for one frame: no composed pixel is transparent, so no alpha channel is claimed that the file lacks
        frames = [Raster(np.ascontiguousarray(f.pixels[:, :, :3]), "rgb", 8) for f in frames]
        notes.append("frames are the composed logical screen (RGB: no pixel of any frame is transparent)")
    else:
        notes.append("frames are the composed logical screen (RGBA)")
    return Decoded(detected, R.Frames(tuple(frames), tuple(delays), words=text), tuple(notes), False)


class GIFAdapter(Adapter):
    name = "b2-gif"
    formats = ("gif",)
    families = ("image",)

    def decode(self, data, detected, limits):
        return decode_gif(data, detected, limits)


ADAPTERS = (GIFAdapter(),)

__all__ = ["ADAPTERS", "GIFAdapter", "decode_gif", "lzw_decode"]
