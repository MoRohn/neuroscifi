"""Independent encoders for lane B2's decoder tests: each builds a file byte by byte from a reference written in the
test, in plain Python (struct, zlib), sharing no code with `neuroscifi.artifacts`. The decoders are judged by
recovering the reference."""
from __future__ import annotations

import struct
import zlib

# ---------------------------------------------------------------------------------------------------------------- PNG


def chunk(ctype: bytes, body: bytes) -> bytes:
    return struct.pack(">I", len(body)) + ctype + body + struct.pack(">I", zlib.crc32(ctype + body) & 0xFFFFFFFF)


def _pack_row(samples, bits):
    """A list of sample ints -> packed bytes, big-endian, MSB first."""
    if bits == 16:
        return b"".join(struct.pack(">H", s) for s in samples)
    if bits == 8:
        return bytes(samples)
    out, acc, n = bytearray(), 0, 0
    for s in samples:
        acc = (acc << bits) | s
        n += bits
        if n == 8:
            out.append(acc)
            acc, n = 0, 0
    if n:
        out.append(acc << (8 - n))
    return bytes(out)


def _paeth(a, b, c):
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    return b if pb <= pc else c


def _filter_row(ft, row, prev, bpp):
    out = bytearray([ft])
    for i, x in enumerate(row):
        a = row[i - bpp] if i >= bpp else 0
        b = prev[i] if prev else 0
        c = prev[i - bpp] if (prev and i >= bpp) else 0
        pred = [0, a, b, (a + b) // 2, _paeth(a, b, c)][ft]
        out.append((x - pred) & 0xFF)
    return bytes(out)


def _filtered(image, bits, ch, filters):
    """image: list of rows, each a list of pixels, each a tuple of `ch` samples."""
    bpp = max(1, bits * ch // 8)
    out, prev = b"", None
    for y, row in enumerate(image):
        raw = _pack_row([s for px in row for s in px], bits)
        out += _filter_row(filters[y % len(filters)], raw, prev, bpp)
        prev = raw
    return out


ADAM7 = ((0, 0, 8, 8), (4, 0, 8, 8), (0, 4, 4, 8), (2, 0, 4, 4), (0, 2, 2, 4), (1, 0, 2, 2), (0, 1, 1, 2))


def png(image, *, bits=8, ctype=2, filters=(0,), interlace=0, palette=None, trns=None, extra=(), compress_level=6,
        stream=None):
    """A PNG of `image` (rows of pixel tuples). `stream` overrides the zlib stream (for hostile tests)."""
    h, w = len(image), len(image[0])
    ch = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[ctype]
    if interlace:
        data = b""
        for x0, y0, dx, dy in ADAM7:
            sub = [row[x0::dx] for row in image[y0::dy]]
            sub = [r for r in sub if r]
            if sub:
                data += _filtered(sub, bits, ch, filters)
    else:
        data = _filtered(image, bits, ch, filters)
    body = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, bits, ctype, 0, 0, interlace))
    if palette is not None:
        body += chunk(b"PLTE", bytes(v for rgb in palette for v in rgb))
    if trns is not None:
        body += chunk(b"tRNS", trns)
    for c in extra:
        body += c
    body += chunk(b"IDAT", stream if stream is not None else zlib.compress(data, compress_level))
    return body + chunk(b"IEND", b"")


# ---------------------------------------------------------------------------------------------------------------- WAV


def riff(form: bytes, chunks) -> bytes:
    body = form
    for cid, data in chunks:
        body += cid + struct.pack("<I", len(data)) + data + (b"\x00" if len(data) % 2 else b"")
    return b"RIFF" + struct.pack("<I", len(body)) + body


def fmt_chunk(tag, channels, rate, bits, extensible_subtype=None):
    block = channels * bits // 8
    base = struct.pack("<HHIIHH", tag, channels, rate, rate * block, block, bits)
    if extensible_subtype is None:
        return base
    guid = struct.pack("<H", extensible_subtype) + b"\x00\x00\x00\x00\x10\x00\x80\x00\x00\xaa\x00\x38\x9b\x71"
    return base + struct.pack("<HHI", 22, bits, 0) + guid
