"""A small PNG and animated-PNG writer (lane G2): signature, chunks with CRC-32, IHDR, iTXt, IDAT, and for an
animation acTL / fcTL / fdAT, IEND. No interlacing, no palette, every scanline with filter 0 (None), compressed by
zlib with fixed settings (level 9, 32 KiB window, memLevel 9, default strategy), and no time chunk, so the same
pixels give the same bytes for a given zlib. (A host whose zlib is a different implementation, such as zlib-ng, may
compress to other, equally valid bytes: the pixels and every other chunk stay identical.)"""
from __future__ import annotations

import struct
import zlib

import numpy as np

SIGNATURE = b"\x89PNG\r\n\x1a\n"
COLOR_TYPES = {1: 0, 2: 4, 3: 2, 4: 6}      # channels -> PNG colour type: gray, gray+alpha, RGB, RGBA


def chunk(kind: bytes, body: bytes) -> bytes:
    return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)


def _channels(pixels: np.ndarray) -> int:
    return 1 if pixels.ndim == 2 else int(pixels.shape[2])


def scanlines(pixels: np.ndarray) -> bytes:
    """Rows of big-endian samples, each preceded by the filter byte 0."""
    h, w = pixels.shape[:2]
    dtype = ">u2" if pixels.dtype == np.uint16 else "u1"
    rows = np.ascontiguousarray(pixels.astype(dtype)).reshape(h, -1).view(np.uint8)
    return np.concatenate([np.zeros((h, 1), dtype=np.uint8), rows], axis=1).tobytes()


def compress(raw: bytes) -> bytes:
    z = zlib.compressobj(9, zlib.DEFLATED, 15, 9, zlib.Z_DEFAULT_STRATEGY)
    return z.compress(raw) + z.flush()


def itxt(keyword: str, text: str) -> bytes:
    """An uncompressed international text chunk (UTF-8)."""
    key = keyword.encode("latin-1")[:79]
    return chunk(b"iTXt", key + b"\0" + b"\0\0" + b"\0" + b"\0" + text.encode("utf-8"))


def _ihdr(width: int, height: int, depth: int, channels: int) -> bytes:
    return chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, depth, COLOR_TYPES[channels], 0, 0, 0))


def png(pixels: np.ndarray, texts: list[tuple[str, str]] = ()) -> bytes:
    h, w = pixels.shape[:2]
    depth = 16 if pixels.dtype == np.uint16 else 8
    return (SIGNATURE + _ihdr(w, h, depth, _channels(pixels)) + b"".join(itxt(k, t) for k, t in texts)
            + chunk(b"IDAT", compress(scanlines(pixels))) + chunk(b"IEND", b""))


def apng(frames: list[np.ndarray], delays_ms: list[int], plays: int = 0, texts: list[tuple[str, str]] = ()) -> bytes:
    """Every frame covers the whole canvas (offset 0, 0), is drawn by replacing (blend op 0) and left in place
    (dispose op 0); the first frame is also the still image a viewer without animation shows."""
    first = frames[0]
    h, w = first.shape[:2]
    depth = 16 if first.dtype == np.uint16 else 8
    out = [SIGNATURE, _ihdr(w, h, depth, _channels(first)), chunk(b"acTL", struct.pack(">II", len(frames), plays))]
    out += [itxt(k, t) for k, t in texts]
    seq = 0
    for k, (frame, delay) in enumerate(zip(frames, delays_ms, strict=True)):
        out.append(chunk(b"fcTL", struct.pack(">IIIIIHHBB", seq, w, h, 0, 0, int(delay), 1000, 0, 0)))
        seq += 1
        data = compress(scanlines(frame))
        if k == 0:
            out.append(chunk(b"IDAT", data))
        else:
            out.append(chunk(b"fdAT", struct.pack(">I", seq) + data))
            seq += 1
    out.append(chunk(b"IEND", b""))
    return b"".join(out)


__all__ = ["SIGNATURE", "apng", "chunk", "compress", "itxt", "png", "scanlines"]
