"""The benchmark's own encoders: every file a task hands over is assembled here, byte by byte, from values the
family's generator chose, so the true content is known by construction.

Independence: nothing here imports product code (`neuroscifi.artifacts`, `neuroscifi.webcortex`, ...) or an optional
package. Compression is written out by hand (deflate with stored, fixed-Huffman and one dynamic-Huffman block shape;
zlib, gzip, zip and zstd framing) rather than taken from `zlib.compress`, so that the bytes, and therefore the frozen
task hashes, are the same on every host whatever zlib it links. `zlib` is used only for its checksums (crc32,
adler32), which are fixed functions. tarfile, struct and json are deterministic for fixed inputs.
"""
from __future__ import annotations

import io
import json
import struct
import tarfile
import zlib

import numpy as np

# ---- deflate (RFC 1951) ----------------------------------------------------------------------------------------------

_LEN_BASE = (3, 4, 5, 6, 7, 8, 9, 10, 11, 13, 15, 17, 19, 23, 27, 31, 35, 43, 51, 59, 67, 83, 99, 115, 131, 163, 195,
             227, 258)
_LEN_EXTRA = (0,) * 8 + (1,) * 4 + (2,) * 4 + (3,) * 4 + (4,) * 4 + (5,) * 4 + (0,)
_DIST_BASE = (1, 2, 3, 4, 5, 7, 9, 13, 17, 25, 33, 49, 65, 97, 129, 193, 257, 385, 513, 769, 1025, 1537, 2049, 3073,
              4097, 6145, 8193, 12289, 16385, 24577)
_DIST_EXTRA = (0, 0, 0, 0, 1, 1, 2, 2, 3, 3, 4, 4, 5, 5, 6, 6, 7, 7, 8, 8, 9, 9, 10, 10, 11, 11, 12, 12, 13, 13)
_CL_ORDER = (16, 17, 18, 0, 8, 7, 9, 6, 10, 5, 11, 4, 12, 3, 13, 2, 14, 1, 15)


class BitWriter:
    """Bits packed least-significant first, as deflate wants; Huffman codes are written most-significant first."""

    def __init__(self):
        self.out = bytearray()
        self.acc = 0
        self.n = 0

    def bits(self, value: int, n: int) -> None:
        self.acc |= value << self.n
        self.n += n
        while self.n >= 8:
            self.out.append(self.acc & 0xFF)
            self.acc >>= 8
            self.n -= 8

    def code(self, code: int, length: int) -> None:
        rev = 0
        for _ in range(length):
            rev = (rev << 1) | (code & 1)
            code >>= 1
        self.bits(rev, length)

    def zeros(self, n: int) -> None:
        head = (8 - self.n) % 8
        if n < head + 8:
            while n > 0:
                step = min(n, 16)
                self.bits(0, step)
                n -= step
            return
        self.bits(0, head)
        self.out.extend(bytes((n - head) // 8))
        self.bits(0, (n - head) % 8)

    def flush(self) -> bytes:
        if self.n:
            self.out.append(self.acc & 0xFF)
            self.acc = self.n = 0
        return bytes(self.out)


def _canonical(lengths) -> dict[int, tuple[int, int]]:
    """symbol -> (code, length) for the canonical Huffman code with these lengths (0 = unused)."""
    max_len = max(lengths) if lengths else 0
    bl_count = [0] * (max_len + 1)
    for length in lengths:
        if length:
            bl_count[length] += 1
    next_code, code = [0] * (max_len + 2), 0
    for bits in range(1, max_len + 1):
        code = (code + bl_count[bits - 1]) << 1
        next_code[bits] = code
    codes = {}
    for symbol, length in enumerate(lengths):
        if length:
            codes[symbol] = (next_code[length], length)
            next_code[length] += 1
    return codes


def _huffman_lengths(freqs: dict[int, int], size: int, limit: int) -> list[int]:
    """Code lengths of a (complete) Huffman code for the used symbols; the alphabets used here are tiny."""
    import heapq
    used = [s for s, f in freqs.items() if f]
    lengths = [0] * size
    if len(used) == 1:
        lengths[used[0]] = 1
        return lengths
    heap = [(freqs[s], i, (s,)) for i, s in enumerate(sorted(used))]
    heapq.heapify(heap)
    order = len(heap)
    while len(heap) > 1:
        f1, _, a = heapq.heappop(heap)
        f2, _, b = heapq.heappop(heap)
        for s in a + b:
            lengths[s] += 1
        heapq.heappush(heap, (f1 + f2, order, a + b))
        order += 1
    if max(lengths) > limit:
        raise ValueError("code too long for this tiny encoder")
    return lengths


def _fixed_litlen(symbol: int) -> tuple[int, int]:
    if symbol < 144:
        return 0x30 + symbol, 8
    if symbol < 256:
        return 0x190 + symbol - 144, 9
    if symbol < 280:
        return symbol - 256, 7
    return 0xC0 + symbol - 280, 8


def _length_symbol(length: int) -> tuple[int, int, int]:
    for i in range(len(_LEN_BASE) - 1, -1, -1):
        if length >= _LEN_BASE[i]:
            if i == 28 and length != 258:
                continue
            return 257 + i, length - _LEN_BASE[i], _LEN_EXTRA[i]
    raise ValueError(length)


def _dist_symbol(dist: int) -> tuple[int, int, int]:
    for i in range(len(_DIST_BASE) - 1, -1, -1):
        if dist >= _DIST_BASE[i]:
            return i, dist - _DIST_BASE[i], _DIST_EXTRA[i]
    raise ValueError(dist)


def deflate_stored(data: bytes) -> bytes:
    w = BitWriter()
    chunks = [data[i:i + 65535] for i in range(0, len(data), 65535)] or [b""]
    for i, chunk in enumerate(chunks):
        w.bits(1 if i == len(chunks) - 1 else 0, 1)
        w.bits(0, 2)
        w.flush()
        w.out.extend(struct.pack("<HH", len(chunk), len(chunk) ^ 0xFFFF))
        w.out.extend(chunk)
    return w.flush()


def deflate_fixed(data: bytes) -> bytes:
    """One final fixed-Huffman block; greedy LZ77 over a 32 KiB window (most recent position per 3-byte prefix)."""
    w = BitWriter()
    w.bits(1, 1)
    w.bits(1, 2)
    table: dict[bytes, int] = {}
    i, n = 0, len(data)
    while i < n:
        best = 0
        if i + 3 <= n:
            key = data[i:i + 3]
            j = table.get(key)
            if j is not None and i - j <= 32768:
                length = 3
                limit = min(258, n - i)
                while length < limit and data[j + length] == data[i + length]:
                    length += 1
                best, dist = length, i - j
        if best >= 3:
            sym, extra, nbits = _length_symbol(best)
            w.code(*_fixed_litlen(sym))
            w.bits(extra, nbits)
            dsym, dextra, dbits = _dist_symbol(dist)
            w.code(dsym, 5)
            w.bits(dextra, dbits)
            for k in range(i, i + best):
                if k + 3 <= n:
                    table[data[k:k + 3]] = k
            i += best
        else:
            if i + 3 <= n:
                table[data[i:i + 3]] = i
            w.code(*_fixed_litlen(data[i]))
            i += 1
    w.code(*_fixed_litlen(256))
    return w.flush()


def deflate_run(n: int, byte: int = 0) -> bytes:
    """`n` copies of one byte as a single dynamic-Huffman block: one literal, then length-258 matches at distance 1
    costing 2 bits each (about 1000:1), the remainder as literals. The shape of a decompression bomb."""
    if n < 1:
        raise ValueError("n >= 1")
    lit = [0] * 286
    lit[byte], lit[256], lit[285] = 2, 2, 1
    dist = [1]
    w = BitWriter()
    w.bits(1, 1)
    w.bits(2, 2)
    _dynamic_header(w, lit, dist)
    litc, distc = _canonical(lit), _canonical(dist)
    w.code(*litc[byte])
    matches, rest = divmod(n - 1, 258)
    if litc[285] == (0, 1) and distc[0] == (0, 1):
        w.zeros(2 * matches)
    else:                                   # not reached with the lengths above; kept exact for safety
        for _ in range(matches):
            w.code(*litc[285])
            w.code(*distc[0])
    for _ in range(rest):
        w.code(*litc[byte])
    w.code(*litc[256])
    return w.flush()


def _dynamic_header(w: BitWriter, lit: list[int], dist: list[int]) -> None:
    lengths = lit + dist
    rle: list[tuple[int, int, int]] = []          # (symbol, extra value, extra bits)
    i = 0
    while i < len(lengths):
        v = lengths[i]
        run = 1
        while i + run < len(lengths) and lengths[i + run] == v:
            run += 1
        i += run
        if v == 0:
            while run >= 11:
                take = min(run, 138)
                rle.append((18, take - 11, 7))
                run -= take
            if run >= 3:
                rle.append((17, run - 3, 3))
                run = 0
        rle.extend((v, 0, 0) for _ in range(run))
    freqs: dict[int, int] = {}
    for sym, _, _ in rle:
        freqs[sym] = freqs.get(sym, 0) + 1
    cl = _huffman_lengths(freqs, 19, 7)
    clc = _canonical(cl)
    hclen = max(k for k in range(19) if cl[_CL_ORDER[k]]) + 1
    hclen = max(hclen, 4)
    w.bits(len(lit) - 257, 5)
    w.bits(len(dist) - 1, 5)
    w.bits(hclen - 4, 4)
    for k in range(hclen):
        w.bits(cl[_CL_ORDER[k]], 3)
    for sym, extra, nbits in rle:
        w.code(*clc[sym])
        if nbits:
            w.bits(extra, nbits)


def deflate(data: bytes, *, level: int = 1) -> bytes:
    return deflate_stored(data) if level == 0 else deflate_fixed(data)


def zlib_stream(data: bytes, *, level: int = 1, raw: bytes | None = None) -> bytes:
    """A zlib stream (RFC 1950) around deflate data; `raw` gives a precomputed deflate body for `data`."""
    body = raw if raw is not None else deflate(data, level=level)
    return b"\x78\x01" + body + struct.pack(">I", zlib.adler32(data) & 0xFFFFFFFF)


def gzip_member(data: bytes, *, level: int = 1, name: str | None = None, body: bytes | None = None,
                size: int | None = None, crc: int | None = None) -> bytes:
    """A gzip member (RFC 1952), mtime 0. For a bomb pass `body` (deflate data), `size` and `crc` directly."""
    flags = 0x08 if name else 0
    head = struct.pack("<BBBBIBB", 0x1F, 0x8B, 8, flags, 0, 0, 255)
    if name:
        head += name.encode("latin-1") + b"\x00"
    body = body if body is not None else deflate(data, level=level)
    crc = crc if crc is not None else zlib.crc32(data) & 0xFFFFFFFF
    size = size if size is not None else len(data)
    return head + body + struct.pack("<II", crc, size & 0xFFFFFFFF)


def crc_of_run(n: int, byte: int = 0) -> int:
    """CRC-32 of `n` copies of one byte, computed in 1 MiB steps (no n-byte buffer)."""
    block = bytes([byte]) * min(n, 1 << 20)
    crc, left = 0, n
    while left:
        step = min(left, len(block))
        crc = zlib.crc32(block[:step], crc)
        left -= step
    return crc & 0xFFFFFFFF


def zstd_raw_frame(data: bytes) -> bytes:
    """A Zstandard frame (RFC 8878) made of raw (uncompressed) blocks, single segment, content size stated."""
    out = bytearray(b"\x28\xB5\x2F\xFD")
    size = len(data)
    if size < 256:
        out.append(0x20)                     # single segment, FCS field 1 byte
        out.append(size)
    elif size < 65536 + 256:
        out.append(0x60)                     # FCS 2 bytes (value - 256)
        out += struct.pack("<H", size - 256)
    else:
        out.append(0xA0)
        out += struct.pack("<I", size)
    blocks = [data[i:i + 131072] for i in range(0, size, 131072)] or [b""]
    for i, block in enumerate(blocks):
        last = 1 if i == len(blocks) - 1 else 0
        header = last | (0 << 1) | (len(block) << 3)
        out += struct.pack("<I", header)[:3]
        out += block
    return bytes(out)


# ---- zip (APPNOTE 6.3), written by hand so that hostile shapes can be built too ----------------------------------------

_DOS_TIME, _DOS_DATE = 0, (2020 - 1980) << 9 | 1 << 5 | 1      # 2020-01-01 00:00:00, fixed


def zip_archive(members, *, comment: bytes = b"") -> bytes:
    """members: iterable of dicts {name, data, method ('store'|'deflate'), body (raw deflate body, optional),
    size (uncompressed size to declare, optional), crc (optional), external_attr (optional), create_system
    (optional, 3 = unix), local_name (a different name in the local header, optional), offset_of (index of an earlier
    member whose local header this central entry points at: overlapping entries)}."""
    out = bytearray()
    central = bytearray()
    offsets: list[int] = []
    count = 0
    for m in members:
        name = m["name"].encode("utf-8")
        data = m.get("data", b"")
        method = 8 if m.get("method", "deflate") == "deflate" else 0
        if m.get("body") is not None:
            body = m["body"]
        else:
            body = deflate(data) if method == 8 else data
        crc = m["crc"] if m.get("crc") is not None else zlib.crc32(data) & 0xFFFFFFFF
        size = m["size"] if m.get("size") is not None else len(data)
        flags = 0x800 if any(b > 127 for b in name) else 0
        if m.get("offset_of") is not None:
            offset = offsets[m["offset_of"]]
            comp = m["comp_size"]
        else:
            offset = len(out)
            local_name = m.get("local_name", m["name"]).encode("utf-8")
            out += struct.pack("<IHHHHHIIIHH", 0x04034B50, 20, flags, method, _DOS_TIME, _DOS_DATE, crc, len(body),
                               size, len(local_name), 0)
            out += local_name + body
            comp = len(body)
        offsets.append(offset)
        system = m.get("create_system", 0)
        central += struct.pack("<IHHHHHHIIIHHHHHII", 0x02014B50, (system << 8) | 20, 20, flags, method, _DOS_TIME,
                               _DOS_DATE, crc, comp, size, len(name), 0, 0, 0, 0, m.get("external_attr", 0), offset)
        central += name
        count += 1
    start = len(out)
    out += central
    out += struct.pack("<IHHHHIIH", 0x06054B50, 0, 0, count & 0xFFFF, count & 0xFFFF, len(central), start,
                       len(comment))
    out += comment
    return bytes(out)


def zip_files(files: dict[str, bytes], *, method: str = "deflate", first_stored: str | None = None) -> bytes:
    members = []
    for name, data in files.items():
        members.append({"name": name, "data": data, "method": "store" if name == first_stored else method})
    return zip_archive(members)


def tar_archive(files: dict[str, bytes], *, symlinks: dict[str, str] | None = None) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.USTAR_FORMAT) as tar:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size, info.mtime, info.mode, info.uname, info.gname = len(data), 0, 0o644, "", ""
            tar.addfile(info, io.BytesIO(data))
        for name, target in (symlinks or {}).items():
            info = tarfile.TarInfo(name)
            info.type, info.linkname, info.mtime, info.mode = tarfile.SYMTYPE, target, 0, 0o777
            tar.addfile(info)
    return buf.getvalue()


# ---- images ------------------------------------------------------------------------------------------------------------

def _chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)


_COLOR_TYPE = {"gray": 0, "rgb": 2, "palette": 3, "gray_alpha": 4, "rgba": 6}
_CHANNELS = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}
ADAM7 = ((0, 0, 8, 8), (4, 0, 8, 8), (0, 4, 4, 8), (2, 0, 4, 4), (0, 2, 2, 4), (1, 0, 2, 2), (0, 1, 1, 2))


def _paeth(a: int, b: int, c: int) -> int:
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    return b if pb <= pc else c


def _pack_row(row: np.ndarray, bits: int) -> bytes:
    """One scanline's samples (1-D, already interleaved channels) to bytes at the bit depth."""
    if bits == 16:
        return row.astype(">u2").tobytes()
    if bits == 8:
        return row.astype(np.uint8).tobytes()
    per = 8 // bits
    out = bytearray()
    vals = [int(v) for v in row]
    for i in range(0, len(vals), per):
        byte = 0
        for k in range(per):
            v = vals[i + k] if i + k < len(vals) else 0
            byte |= v << (8 - bits * (k + 1))
        out.append(byte)
    return bytes(out)


def _filter_rows(rows: list[bytes], bpp: int, start: int) -> bytes:
    """Filter each scanline with type (start + index) % 5, so every filter type is exercised."""
    out = bytearray()
    prev = bytes(len(rows[0])) if rows else b""
    for i, raw in enumerate(rows):
        ftype = (start + i) % 5
        line = bytearray([ftype])
        for x in range(len(raw)):
            a = raw[x - bpp] if x >= bpp else 0
            b = prev[x]
            c = prev[x - bpp] if x >= bpp else 0
            pred = (0, a, b, (a + b) // 2, _paeth(a, b, c))[ftype]
            line.append((raw[x] - pred) & 0xFF)
        out += line
        prev = raw
    return bytes(out)


def _image_data(pixels: np.ndarray, color_type: int, bits: int, interlace: bool, filter_start: int = 0) -> bytes:
    h, w = pixels.shape[:2]
    channels = _CHANNELS[color_type]
    flat = pixels.reshape(h, w, channels)
    bpp = max(1, channels * bits // 8)
    if not interlace:
        rows = [_pack_row(flat[y].reshape(-1), bits) for y in range(h)]
        return _filter_rows(rows, bpp, filter_start)
    out = bytearray()
    for p, (x0, y0, dx, dy) in enumerate(ADAM7):
        sub = flat[y0::dy, x0::dx]
        if sub.shape[0] == 0 or sub.shape[1] == 0:
            continue
        rows = [_pack_row(sub[y].reshape(-1), bits) for y in range(sub.shape[0])]
        out += _filter_rows(rows, bpp, filter_start + p)
    return bytes(out)


def png(pixels: np.ndarray, *, mode: str, bits: int = 8, palette=None, transparency=None, interlace: bool = False,
        level: int = 1, extra_chunks=(), idat_parts: int = 1, filter_start: int = 0) -> bytes:
    """A PNG. pixels: (h, w) for gray/palette (palette: indices), (h, w, c) otherwise; palette: list of (r, g, b);
    transparency: bytes of the tRNS chunk; extra_chunks: (type, data) placed before IDAT."""
    color_type = _COLOR_TYPE[mode]
    h, w = pixels.shape[:2]
    out = b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, bits, color_type, 0, 0,
                                                             1 if interlace else 0))
    for kind, data in extra_chunks:
        out += _chunk(kind, data)
    if palette is not None:
        out += _chunk(b"PLTE", b"".join(bytes(c) for c in palette))
    if transparency is not None:
        out += _chunk(b"tRNS", transparency)
    stream = zlib_stream(_image_data(pixels, color_type, bits, interlace, filter_start), level=level)
    step = -(-len(stream) // idat_parts)
    for i in range(0, len(stream), step):
        out += _chunk(b"IDAT", stream[i:i + step])
    return out + _chunk(b"IEND", b"")


def apng(frames: list[np.ndarray], delays: list[tuple[int, int]], *, mode: str = "gray", plays: int = 0) -> bytes:
    """An animated PNG: every frame full size at offset 0, dispose none, blend source. delays: (num, den)."""
    color_type = _COLOR_TYPE[mode]
    h, w = frames[0].shape[:2]
    out = b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, color_type, 0, 0, 0))
    out += _chunk(b"acTL", struct.pack(">II", len(frames), plays))
    seq = 0
    for i, (frame, (num, den)) in enumerate(zip(frames, delays)):
        out += _chunk(b"fcTL", struct.pack(">IIIIIHHBB", seq, w, h, 0, 0, num, den, 0, 0))
        seq += 1
        stream = zlib_stream(_image_data(frame, color_type, 8, False))
        if i == 0:
            out += _chunk(b"IDAT", stream)
        else:
            out += _chunk(b"fdAT", struct.pack(">I", seq) + stream)
            seq += 1
    return out + _chunk(b"IEND", b"")


def pnm(pixels: np.ndarray, *, maxval: int = 255, ascii_form: bool = False, comment: str | None = None) -> bytes:
    """PGM (h, w) or PPM (h, w, 3); binary P5/P6 (16-bit big-endian when maxval > 255) or ASCII P2/P3."""
    color = pixels.ndim == 3
    magic = ("P3" if color else "P2") if ascii_form else ("P6" if color else "P5")
    h, w = pixels.shape[:2]
    head = magic + "\n" + (f"# {comment}\n" if comment else "") + f"{w} {h}\n{maxval}\n"
    if ascii_form:
        flat = pixels.reshape(h, -1)
        body = "\n".join(" ".join(str(int(v)) for v in row) for row in flat) + "\n"
        return head.encode("ascii") + body.encode("ascii")
    dtype = ">u2" if maxval > 255 else np.uint8
    return head.encode("ascii") + pixels.astype(dtype).tobytes()


def bmp(pixels: np.ndarray, *, top_down: bool = False, bits: int = 24) -> bytes:
    """A BMP with a BITMAPINFOHEADER: 24-bit BGR rows padded to 4 bytes, or 32-bit BGRA; bottom-up unless top_down."""
    h, w = pixels.shape[:2]
    rows = []
    for y in range(h):
        row = pixels[y]
        if bits == 24:
            raw = row[:, [2, 1, 0]].astype(np.uint8).tobytes()
            raw += bytes((-len(raw)) % 4)
        else:
            raw = row[:, [2, 1, 0, 3]].astype(np.uint8).tobytes()
        rows.append(raw)
    if not top_down:
        rows.reverse()
    data = b"".join(rows)
    header = struct.pack("<IiiHHIIiiII", 40, w, -h if top_down else h, 1, bits, 0, len(data), 2835, 2835, 0, 0)
    file_head = struct.pack("<2sIHHI", b"BM", 14 + 40 + len(data), 0, 0, 14 + 40)
    return file_head + header + data


def _lzw(indices: list[int], min_size: int) -> bytes:
    clear, end = 1 << min_size, (1 << min_size) + 1
    w = BitWriter()
    size = min_size + 1
    table = {(i,): i for i in range(clear)}
    next_code = end + 1
    w.bits(clear, size)
    current: tuple[int, ...] = ()
    for px in indices:
        candidate = current + (px,)
        if candidate in table:
            current = candidate
            continue
        w.bits(table[current], size)
        if next_code < 4096:
            table[candidate] = next_code
            next_code += 1
            if next_code - 1 == (1 << size) and size < 12:
                size += 1
        else:
            w.bits(clear, size)
            table = {(i,): i for i in range(clear)}
            next_code, size = end + 1, min_size + 1
        current = (px,)
    if current:
        w.bits(table[current], size)
    w.bits(end, size)
    return w.flush()


def gif(frames: list[np.ndarray], palette: list[tuple[int, int, int]], delays_cs: list[int]) -> bytes:
    """A GIF89a: global palette (padded to a power of two), a looping extension, one graphic control extension and
    one LZW-coded image per frame. frames: (h, w) palette indices."""
    h, w = frames[0].shape
    n = max(2, len(palette))
    depth = max(1, (n - 1).bit_length())
    table = list(palette) + [(0, 0, 0)] * ((1 << depth) - len(palette))
    out = bytearray(b"GIF89a")
    out += struct.pack("<HHBBB", w, h, 0x80 | 0x70 | (depth - 1), 0, 0)
    out += b"".join(bytes(c) for c in table)
    out += b"\x21\xFF\x0BNETSCAPE2.0\x03\x01\x00\x00\x00"
    min_size = max(2, depth)
    for frame, delay in zip(frames, delays_cs):
        out += b"\x21\xF9\x04" + struct.pack("<BHB", 0x04, delay, 0) + b"\x00"
        out += b"\x2C" + struct.pack("<HHHHB", 0, 0, w, h, 0)
        data = _lzw([int(v) for v in frame.reshape(-1)], min_size)
        out.append(min_size)
        for i in range(0, len(data), 255):
            block = data[i:i + 255]
            out.append(len(block))
            out += block
        out.append(0)
    out += b"\x3B"
    return bytes(out)


# ---- sound -------------------------------------------------------------------------------------------------------------

def wav(samples: np.ndarray, rate: int, *, bits: int = 16, extensible: bool = False, extra_chunks=()) -> bytes:
    """PCM WAV from float samples in [-1, 1], shape (n,) or (n, channels). 8-bit is unsigned (offset 128); 16/24/32
    are signed little-endian. Quantisation: round(x * (2**(bits-1) - 1))."""
    x = samples if samples.ndim == 2 else samples[:, None]
    channels = x.shape[1]
    full = (1 << (bits - 1)) - 1
    q = np.rint(np.clip(x, -1.0, 1.0) * full).astype(np.int64)
    if bits == 8:
        data = (q + 128).astype(np.uint8).tobytes()
    elif bits == 16:
        data = q.astype("<i2").tobytes()
    elif bits == 24:
        flat = q.reshape(-1)
        data = b"".join(int(v & 0xFFFFFF).to_bytes(3, "little") for v in flat)
    else:
        data = q.astype("<i4").tobytes()
    block = channels * bits // 8
    if extensible:
        fmt = struct.pack("<HHIIHHHHI16s", 0xFFFE, channels, rate, rate * block, block, bits, 22, bits,
                          (1 << channels) - 1,
                          b"\x01\x00\x00\x00\x00\x00\x10\x00\x80\x00\x00\xaa\x00\x38\x9b\x71")
    else:
        fmt = struct.pack("<HHIIHH", 1, channels, rate, rate * block, block, bits)
    body = b"WAVE" + _riff_chunk(b"fmt ", fmt)
    for kind, payload in extra_chunks:
        body += _riff_chunk(kind, payload)
    body += _riff_chunk(b"data", data)
    return b"RIFF" + struct.pack("<I", len(body)) + body


def _riff_chunk(kind: bytes, data: bytes) -> bytes:
    return kind + struct.pack("<I", len(data)) + data + (b"\x00" if len(data) % 2 else b"")


def quantised(samples: np.ndarray, bits: int) -> np.ndarray:
    """The float values a correct reader recovers from wav(samples, bits=bits): q / (2**(bits-1) - 1) (8-bit: the
    unsigned offset removed first). Used to state truths that survive quantisation."""
    full = (1 << (bits - 1)) - 1
    return np.rint(np.clip(samples, -1.0, 1.0) * full) / full


# ---- meshes ------------------------------------------------------------------------------------------------------------

def _fmt(v: float) -> str:
    return repr(float(v))


def obj(vertices, faces, *, comment: str | None = None) -> bytes:
    """Wavefront OBJ: faces are 0-based index lists (triangles or polygons); written 1-based."""
    lines = [f"# {comment}"] if comment else []
    lines += ["v " + " ".join(_fmt(c) for c in v) for v in vertices]
    lines += ["f " + " ".join(str(i + 1) for i in f) for f in faces]
    return ("\n".join(lines) + "\n").encode("ascii")


def _normal(a, b, c):
    n = np.cross(np.subtract(b, a), np.subtract(c, a))
    length = float(np.linalg.norm(n))
    return n / length if length else n


def stl_ascii(vertices, triangles, *, name: str = "shape") -> bytes:
    out = [f"solid {name}"]
    for t in triangles:
        a, b, c = (vertices[i] for i in t)
        n = _normal(a, b, c)
        out.append(f"  facet normal {_fmt(n[0])} {_fmt(n[1])} {_fmt(n[2])}")
        out.append("    outer loop")
        out += [f"      vertex {_fmt(p[0])} {_fmt(p[1])} {_fmt(p[2])}" for p in (a, b, c)]
        out.append("    endloop")
        out.append("  endfacet")
    out.append(f"endsolid {name}")
    return ("\n".join(out) + "\n").encode("ascii")


def stl_binary(vertices, triangles, *, header: bytes = b"benchmark binary stl") -> bytes:
    out = bytearray(header[:80].ljust(80, b"\x00"))
    out += struct.pack("<I", len(triangles))
    for t in triangles:
        a, b, c = (vertices[i] for i in t)
        n = _normal(a, b, c)
        out += struct.pack("<12fH", *n, *a, *b, *c, 0)
    return bytes(out)


def ply(vertices, faces, *, binary: bool = False) -> bytes:
    head = ["ply", "format " + ("binary_little_endian" if binary else "ascii") + " 1.0",
            f"element vertex {len(vertices)}", "property float x", "property float y", "property float z",
            f"element face {len(faces)}", "property list uchar int vertex_indices", "end_header"]
    out = ("\n".join(head) + "\n").encode("ascii")
    if binary:
        body = bytearray()
        for v in vertices:
            body += struct.pack("<3f", *v)
        for f in faces:
            body += struct.pack("<B", len(f)) + struct.pack(f"<{len(f)}i", *f)
        return out + bytes(body)
    lines = [" ".join(_fmt(c) for c in v) for v in vertices] + [f"{len(f)} " + " ".join(str(i) for i in f)
                                                                 for f in faces]
    return out + ("\n".join(lines) + "\n").encode("ascii")


# ---- arrays ------------------------------------------------------------------------------------------------------------

def npy(array: np.ndarray | None = None, *, descr: str | None = None, shape=None, fortran: bool = False,
        data: bytes | None = None) -> bytes:
    """An .npy file, format 1.0, header written by hand. For hostile files pass descr, shape and data directly."""
    if array is not None:
        descr = descr or array.dtype.str
        shape = array.shape if shape is None else shape
        source = np.asfortranarray(array) if fortran else np.ascontiguousarray(array)
        data = source.astype(descr).tobytes(order="F" if fortran else "C") if data is None else data
    header = "{'descr': '%s', 'fortran_order': %s, 'shape': (%s), }" % (
        descr, "True" if fortran else "False", "".join(f"{int(s)}, " for s in shape).rstrip(" ") if len(shape) != 1
        else f"{int(shape[0])},")
    total = 10 + len(header) + 1
    header += " " * ((-total) % 64) + "\n"
    return b"\x93NUMPY\x01\x00" + struct.pack("<H", len(header)) + header.encode("latin-1") + data


# ---- text containers ---------------------------------------------------------------------------------------------------

def xml_escape(text: str) -> str:
    return (str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;"))


def jdump(obj_) -> bytes:
    return json.dumps(obj_, ensure_ascii=False, sort_keys=False, indent=1).encode("utf-8")
