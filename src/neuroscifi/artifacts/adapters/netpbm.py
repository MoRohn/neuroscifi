"""Simple uncompressed raster formats decoded to pixels: NetPBM P1-P6, PAM (P7), farbfeld, QOI.

NetPBM: plain (ASCII) and raw forms, comments in the header, 1- and 2-byte samples. PBM stores 1 for black; it is
represented as 1-bit grey with 0 black and 1 white. A maxval that is not 2^b - 1 is rescaled to 8 or 16 bits
(rounded) and the notes say so. Only the first image of a multi-image file is decoded. PAM: depth 1-4 with
GRAYSCALE(_ALPHA), RGB(_ALPHA) or BLACKANDWHITE tuple types. farbfeld: 16-bit RGBA, big-endian. QOI: the full
operation set (index, diff, luma, run, RGB, RGBA), 3 or 4 channels; the end marker is checked.

Every size is checked against `max_decoded_bytes` before a pixel buffer exists.
"""
from __future__ import annotations

import re
import struct

import numpy as np

from ..safe import Deadline
from ..safe_b2 import need
from ..types import Decoded, Detected, Limits, Malformed, Raster
from . import Adapter

_TOKEN = re.compile(rb"(?:\s|#[^\n]*(?:\n|$))*(\S+)")


def _tokens(data: bytes, pos: int, n: int) -> tuple[list[bytes], int]:
    out = []
    for _ in range(n):
        m = _TOKEN.match(data, pos)
        if not m:
            raise Malformed("the NetPBM header ends early")
        out.append(m.group(1))
        pos = m.end()
    return out, pos


def _int(tok: bytes, what: str) -> int:
    if not tok.isdigit() or len(tok) > 10:
        raise Malformed(f"the NetPBM {what} is not a number")
    return int(tok)


def _scale(arr: np.ndarray, maxval: int, notes: list) -> tuple[np.ndarray, int]:
    """Samples in 0..maxval -> (array, bits) in a Raster's range."""
    if arr.size and int(arr.max()) > maxval:
        raise Malformed("a NetPBM sample exceeds the maxval")
    bits = max(1, int(maxval).bit_length())
    if maxval == (1 << bits) - 1:
        return arr.astype(np.uint16 if bits > 8 else np.uint8), bits
    target = 16 if maxval > 255 else 8
    notes.append(f"samples rescaled from maxval {maxval} to {target} bits")
    top = (1 << target) - 1
    out = (arr.astype(np.uint64) * top + maxval // 2) // maxval
    return out.astype(np.uint16 if target == 16 else np.uint8), target


def _mode(depth: int, alpha: bool) -> str:
    return {(1, False): "gray", (3, False): "rgb", (4, True): "rgba"}.get((depth, alpha), "")


def decode_netpbm(data: bytes, detected: Detected, limits: Limits) -> Decoded:
    deadline = Deadline(limits.max_seconds)
    if len(data) < 3 or data[0:1] != b"P" or data[1:2] not in b"1234567":
        raise Malformed("the NetPBM magic is missing")
    kind = int(data[1:2])
    notes: list[str] = []
    if kind == 7:
        return _decode_pam(data, detected, limits, notes)
    n_fields = 2 if kind in (1, 4) else 3
    toks, pos = _tokens(data, 2, n_fields)
    w, h = _int(toks[0], "width"), _int(toks[1], "height")
    maxval = 1 if kind in (1, 4) else _int(toks[2], "maxval")
    if not (w and h) or not 1 <= maxval <= 65535:
        raise Malformed("the NetPBM size or maxval is out of range")
    ch = 3 if kind in (3, 6) else 1
    width = 2 if maxval > 255 else 1
    need(w * h * ch * 2 * 3, limits, "the NetPBM image")
    if kind in (1, 2, 3):
        body = data[pos:]
        if kind == 1:
            digits = re.sub(rb"#[^\n]*|\s", b"", body)
            if len(digits) < w * h:
                raise Malformed("the plain PBM body has fewer pixels than its size")
            vals = np.frombuffer(digits[:w * h], np.uint8) - ord("0")
        else:
            words = re.sub(rb"#[^\n]*", b" ", body).split()
            deadline.check("reading plain NetPBM")
            if len(words) < w * h * ch:
                raise Malformed("the plain NetPBM body has fewer samples than its size")
            try:
                vals = np.array(words[:w * h * ch], dtype=np.int64)
            except ValueError:
                raise Malformed("a plain NetPBM sample is not a number") from None
            if len(words) > w * h * ch:
                notes.append("data after the first image was not read")
    else:
        if not data[pos:pos + 1].isspace():
            raise Malformed("the raw NetPBM header does not end with whitespace")
        pos += 1
        if kind == 4:
            rowbytes = (w + 7) // 8
            raw = data[pos:pos + rowbytes * h]
            if len(raw) < rowbytes * h:
                raise Malformed("the raw PBM body is shorter than its size")
            bits = np.unpackbits(np.frombuffer(raw, np.uint8).reshape(h, rowbytes), axis=1)[:, :w]
            vals = bits.reshape(-1)
        else:
            n = w * h * ch * width
            raw = data[pos:pos + n]
            if len(raw) < n:
                raise Malformed("the raw NetPBM body is shorter than its size")
            vals = np.frombuffer(raw, ">u2" if width == 2 else np.uint8)
        end = pos + (((w + 7) // 8) * h if kind == 4 else w * h * ch * width)
        if len(data) > end:
            # a raw NetPBM file may hold further images, each with its own header; any other bytes after the image
            # mean the header was followed by something else, so the image is not answered (lane FX, item 1)
            if not re.match(rb"[ \t\r\n]*(?:P[1-7][ \t\r\n#]|$)", data[end:end + 64]):
                raise Malformed("the bytes after the image are neither another NetPBM image nor white space")
            notes.append("data after the first image was not read")
    if kind in (1, 4):
        if vals.size and int(vals.max()) > 1:
            raise Malformed("a plain PBM pixel is not 0 or 1")
        pixels = (1 - vals.reshape(h, w)).astype(np.uint8)
        notes.append("PBM: 1 means black; represented as 1-bit grey with 0 black and 1 white")
        return Decoded(detected, Raster(pixels, "gray", 1), tuple(notes), False)
    arr, bits = _scale(np.asarray(vals).reshape(h, w, ch) if ch == 3 else np.asarray(vals).reshape(h, w), maxval,
                       notes)
    return Decoded(detected, Raster(np.ascontiguousarray(arr), "rgb" if ch == 3 else "gray", bits), tuple(notes),
                   False)


def _decode_pam(data: bytes, detected: Detected, limits: Limits, notes: list) -> Decoded:
    end = data.find(b"ENDHDR", 0, 4096)
    if end < 0:
        raise Malformed("the PAM header has no ENDHDR")
    fields = {}
    for line in data[2:end].split(b"\n"):
        line = line.split(b"#", 1)[0].strip()
        if line:
            key, _, value = line.partition(b" ")
            fields[key] = value.strip()
    try:
        w, h, depth, maxval = (int(fields[k]) for k in (b"WIDTH", b"HEIGHT", b"DEPTH", b"MAXVAL"))
    except (KeyError, ValueError):
        raise Malformed("the PAM header lacks WIDTH, HEIGHT, DEPTH or MAXVAL") from None
    tupl = fields.get(b"TUPLTYPE", b"").decode("latin-1")
    if not (w > 0 and h > 0 and 1 <= depth <= 4 and 1 <= maxval <= 65535):
        raise Malformed("the PAM header values are out of range")
    width = 2 if maxval > 255 else 1
    need(w * h * max(depth, 4) * 2 * 3, limits, "the PAM image")
    pos = data.find(b"\n", end) + 1
    n = w * h * depth * width
    raw = data[pos:pos + n]
    if pos == 0 or len(raw) < n:
        raise Malformed("the PAM body is shorter than its size")
    vals = np.frombuffer(raw, ">u2" if width == 2 else np.uint8).reshape(h, w, depth)
    if depth == 2:
        vals = np.concatenate([vals[:, :, :1]] * 3 + [vals[:, :, 1:]], axis=2)
        notes.append("grey+alpha represented as RGBA with the grey value in each colour channel")
        depth = 4
    if tupl and tupl not in ("GRAYSCALE", "GRAYSCALE_ALPHA", "RGB", "RGB_ALPHA", "BLACKANDWHITE",
                             "BLACKANDWHITE_ALPHA"):
        notes.append("the tuple type is not a standard one; samples read by depth only")
    arr, bits = _scale(vals[:, :, 0] if depth == 1 else vals, maxval, notes)
    mode = _mode(depth, depth == 4)
    if not mode:
        raise Malformed("the PAM depth does not fit a grey, RGB or RGBA image")
    return Decoded(detected, Raster(np.ascontiguousarray(arr), mode, bits), tuple(notes), False)


def decode_farbfeld(data: bytes, detected: Detected, limits: Limits) -> Decoded:
    if data[:8] != b"farbfeld" or len(data) < 16:
        raise Malformed("the farbfeld header is missing")
    w, h = struct.unpack(">II", data[8:16])
    need(w * h * 8 * 2, limits, "the farbfeld image")
    if len(data) < 16 + w * h * 8:
        raise Malformed("the farbfeld body is shorter than its size")
    px = np.frombuffer(data, ">u2", w * h * 4, 16).reshape(h, w, 4).astype(np.uint16)
    return Decoded(detected, Raster(px, "rgba", 16), (), False)


def decode_qoi(data: bytes, detected: Detected, limits: Limits) -> Decoded:
    deadline = Deadline(limits.max_seconds)
    if data[:4] != b"qoif" or len(data) < 14 + 8:
        raise Malformed("the QOI header is missing")
    w, h, channels, colourspace = struct.unpack(">IIBB", data[4:14])
    if channels not in (3, 4) or colourspace > 1 or not (w and h):
        raise Malformed("the QOI header names invalid channels, colour space or size")
    n = w * h
    need(n * 4 * 3, limits, "the QOI image")
    out = bytearray()
    index = [(0, 0, 0, 0)] * 64
    r, g, b, a = 0, 0, 0, 255
    px = (0, 0, 0, 255)
    p, i, end = 14, 0, len(data) - 8
    while i < n:
        if p >= end:
            raise Malformed("the QOI stream ends before the last pixel")
        b1 = data[p]
        p += 1
        tag = b1 >> 6
        if b1 == 0xFE:
            if p + 3 > end:
                raise Malformed("the QOI stream ends inside a pixel")
            r, g, b = data[p], data[p + 1], data[p + 2]
            p += 3
        elif b1 == 0xFF:
            if p + 4 > end:
                raise Malformed("the QOI stream ends inside a pixel")
            r, g, b, a = data[p], data[p + 1], data[p + 2], data[p + 3]
            p += 4
        elif tag == 0:
            r, g, b, a = index[b1]
        elif tag == 1:
            r = (r + ((b1 >> 4) & 3) - 2) & 0xFF
            g = (g + ((b1 >> 2) & 3) - 2) & 0xFF
            b = (b + (b1 & 3) - 2) & 0xFF
        elif tag == 2:
            b2 = data[p]
            p += 1
            dg = (b1 & 0x3F) - 32
            r = (r + dg - 8 + ((b2 >> 4) & 0xF)) & 0xFF
            g = (g + dg) & 0xFF
            b = (b + dg - 8 + (b2 & 0xF)) & 0xFF
        else:
            run = min((b1 & 0x3F) + 1, n - i)
            out += bytes(px) * run
            i += run
            continue
        px = (r, g, b, a)
        index[(r * 3 + g * 5 + b * 7 + a * 11) % 64] = px
        out += bytes(px)
        i += 1
        if i & 0xFFFF == 0:
            deadline.check("decoding QOI")
    if data[end:] != b"\x00" * 7 + b"\x01" or p != end:
        raise Malformed("the QOI end marker is missing or misplaced")
    px = np.frombuffer(bytes(out), np.uint8).reshape(h, w, 4)
    notes = ("QOI colour space: " + ("all channels linear" if colourspace else "sRGB with linear alpha"),)
    if channels == 3:
        return Decoded(detected, Raster(np.ascontiguousarray(px[:, :, :3]), "rgb", 8), notes, False)
    return Decoded(detected, Raster(px.copy(), "rgba", 8), notes, False)


class NetPBMAdapter(Adapter):
    name = "b2-netpbm"
    formats = ("pbm", "pgm", "ppm", "pam")
    families = ("image",)

    def decode(self, data, detected, limits):
        return decode_netpbm(data, detected, limits)


class FarbfeldAdapter(Adapter):
    name = "b2-farbfeld"
    formats = ("farbfeld",)
    families = ("image",)

    def decode(self, data, detected, limits):
        return decode_farbfeld(data, detected, limits)


class QOIAdapter(Adapter):
    name = "b2-qoi"
    formats = ("qoi",)
    families = ("image",)

    def decode(self, data, detected, limits):
        return decode_qoi(data, detected, limits)


ADAPTERS = (NetPBMAdapter(), FarbfeldAdapter(), QOIAdapter())

__all__ = ["ADAPTERS", "FarbfeldAdapter", "NetPBMAdapter", "QOIAdapter", "decode_farbfeld", "decode_netpbm",
           "decode_qoi"]
