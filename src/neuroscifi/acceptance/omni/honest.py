"""Family `honest`: formats nothing on this host decodes by design (DESIGN.md §8): JPEG, WebP, TIFF, MP3, FLAC,
Ogg Vorbis, MP4, WebM, Parquet, HDF5 and the old binary .doc and .xls. Real files cannot be encoded here without
libraries, so each is assembled by hand: a valid header or container carrying known facts (dimensions, duration,
rate, channels, row count, column names), followed by content that is either absent or not a valid coded stream.

Expected: the right identification, the header facts (kind "measure"), and an abstention for content (kind
"cannot": pixel values, samples, cells, text; any answer is a fabrication). One exception is stated: the baseline
JPEG is REAL (grayscale, one quantisation table of ones, DC-only blocks), so its pixels are known exactly; a
content question on it is kind "cannot_or_value": an abstention or the exact value are both right, anything else
is wrong.
"""
from __future__ import annotations

import struct

import numpy as np

from . import Task, check_split, default_score
from .cases import HONEST
from .intake import ONE_OF, q, rng_for, word

# ---- JPEG (baseline, DC only) -------------------------------------------------------------------------------------


def _segment(marker: int, body: bytes) -> bytes:
    return struct.pack(">HH", marker, len(body) + 2) + body


def jpeg_dc_only(blocks: np.ndarray) -> bytes:
    """A baseline grayscale JPEG whose 8x8 blocks are uniform: block values `blocks` (rows x cols of 0..255).
    Quantisation 1 everywhere, so DC = 8 * (value - 128) exactly and an IDCT returns the value exactly.
    DC table: categories 0..11, all 4-bit codes; AC table: only EOB, code "0"."""
    rows, cols = blocks.shape
    out = b"\xff\xd8" + _segment(0xFFE0, b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00")
    out += _segment(0xFFDB, b"\x00" + bytes([1] * 64))
    out += _segment(0xFFC0, struct.pack(">BHHB", 8, rows * 8, cols * 8, 1) + b"\x01\x11\x00")
    out += _segment(0xFFC4, b"\x00" + bytes([0, 0, 0, 12] + [0] * 12) + bytes(range(12)))
    out += _segment(0xFFC4, b"\x10" + bytes([1] + [0] * 15) + b"\x00")
    out += _segment(0xFFDA, b"\x01\x01\x00\x00\x3f\x00")
    bits: list[str] = []                                # JPEG packs bits most significant first
    prev = 0
    for value in blocks.reshape(-1).tolist():
        dc = 8 * (int(value) - 128)
        diff = dc - prev
        prev = dc
        size = abs(diff).bit_length()
        bits.append(format(size, "04b"))
        if size:
            bits.append(format(diff if diff >= 0 else diff + (1 << size) - 1, f"0{size}b"))
        bits.append("0")                                # EOB
    stream = "".join(bits)
    stream += "1" * ((8 - len(stream) % 8) % 8)
    scan = bytes(int(stream[i:i + 8], 2) for i in range(0, len(stream), 8)).replace(b"\xff", b"\xff\x00")
    return out + scan + b"\xff\xd9"


def jpeg_progressive_header(width: int, height: int, rng) -> bytes:
    """SOF2 (progressive) header with real dimensions, then a scan of noise: identifiable, not decodable."""
    out = b"\xff\xd8" + _segment(0xFFDB, b"\x00" + bytes([2] * 64))
    out += _segment(0xFFC2, struct.pack(">BHHB", 8, height, width, 3) + b"\x01\x22\x00\x02\x11\x00\x03\x11\x00")
    noise = rng.integers(0, 255, 600).astype(np.uint8).tobytes().replace(b"\xff", b"\xfe")
    return out + _segment(0xFFDA, b"\x01\x01\x00\x00\x00\x00") + noise + b"\xff\xd9"


# ---- WebP, TIFF -----------------------------------------------------------------------------------------------------


def webp_header(width: int, height: int, rng) -> bytes:
    vp8x = struct.pack("<I", 0) + (width - 1).to_bytes(3, "little") + (height - 1).to_bytes(3, "little")
    garbage = rng.integers(0, 256, 300).astype(np.uint8).tobytes()
    frame = b"\x9d\x01\x2a" + struct.pack("<HH", width, height)
    tag = struct.pack("<I", (len(garbage) << 5) | 0x10)[:3]       # keyframe, version 0, shown, partition size
    vp8 = tag + frame + garbage
    body = b"WEBP" + b"VP8X" + struct.pack("<I", len(vp8x)) + vp8x + b"VP8 " + struct.pack("<I", len(vp8)) + vp8
    return b"RIFF" + struct.pack("<I", len(body)) + body


def tiff_header(width: int, height: int, rng) -> bytes:
    """Little-endian TIFF, one IFD, compression 7 (JPEG) with a strip of noise."""
    strip = rng.integers(0, 256, 256).astype(np.uint8).tobytes()
    entries = [(256, 3, 1, width), (257, 3, 1, height), (258, 3, 1, 8), (259, 3, 1, 7), (262, 3, 1, 1),
               (273, 4, 1, 0), (277, 3, 1, 1), (278, 3, 1, height), (279, 4, 1, len(strip))]
    ifd_size = 2 + 12 * len(entries) + 4
    strip_offset = 8 + ifd_size
    out = b"II*\x00" + struct.pack("<I", 8) + struct.pack("<H", len(entries))
    for tag, typ, count, value in entries:
        value = strip_offset if tag == 273 else value
        out += struct.pack("<HHI", tag, typ, count) + (struct.pack("<HH", value, 0) if typ == 3
                                                         else struct.pack("<I", value))
    return out + struct.pack("<I", 0) + strip


# ---- MP3, FLAC, Ogg Vorbis ------------------------------------------------------------------------------------------


def mp3_frames(n_frames: int) -> bytes:
    """ID3v2.4 header, then MPEG-1 Layer III frames at 128 kbit/s, 44100 Hz, mono, no padding, all-zero bodies
    (zero side information: silence to a decoder). 417 bytes per frame, 1152 samples per frame."""
    id3 = b"ID3\x04\x00\x00\x00\x00\x00\x00"
    frame = b"\xff\xfb\x90\xc0" + bytes(417 - 4)
    return id3 + frame * n_frames


def flac_streaminfo(rate: int, channels: int, bits: int, total: int) -> bytes:
    info = struct.pack(">HH", 4096, 4096) + (0).to_bytes(3, "big") + (0).to_bytes(3, "big")
    packed = (rate << 44) | ((channels - 1) << 41) | ((bits - 1) << 36) | total
    info += packed.to_bytes(8, "big") + bytes(16)
    return b"fLaC" + bytes([0x80]) + len(info).to_bytes(3, "big") + info


_OGG_TABLE = []
for _i in range(256):
    _r = _i << 24
    for _ in range(8):
        _r = ((_r << 1) ^ 0x04C11DB7) if _r & 0x80000000 else (_r << 1)
    _OGG_TABLE.append(_r & 0xFFFFFFFF)


def ogg_crc(data: bytes) -> int:
    crc = 0
    for b in data:
        crc = ((crc << 8) & 0xFFFFFFFF) ^ _OGG_TABLE[((crc >> 24) & 0xFF) ^ b]
    return crc


def ogg_page(packet: bytes, *, header_type: int, granule: int, serial: int, seq: int) -> bytes:
    segs = [255] * (len(packet) // 255) + [len(packet) % 255]
    head = (b"OggS" + struct.pack("<BBqIII", 0, header_type, granule, serial, seq, 0) + bytes([len(segs)])
            + bytes(segs))
    crc = ogg_crc(head + packet)
    return head[:22] + struct.pack("<I", crc) + head[26:] + packet


def ogg_vorbis(rate: int, channels: int, total: int, rng) -> bytes:
    ident = b"\x01vorbis" + struct.pack("<IBIiiiBB", 0, channels, rate, 0, 128000, 0, 0xB8, 1)
    serial = int(rng.integers(1, 1 << 30))
    first = ogg_page(ident, header_type=0x02, granule=0, serial=serial, seq=0)
    last = ogg_page(rng.integers(0, 256, 40).astype(np.uint8).tobytes(), header_type=0x04, granule=total,
                    serial=serial, seq=1)
    return first + last


# ---- MP4, WebM ---------------------------------------------------------------------------------------------------------


def _box(kind: bytes, body: bytes) -> bytes:
    return struct.pack(">I", 8 + len(body)) + kind + body


_MATRIX = struct.pack(">9I", 0x00010000, 0, 0, 0, 0x00010000, 0, 0, 0, 0x40000000)


def mp4(width: int, height: int, timescale: int, duration: int) -> bytes:
    ftyp = _box(b"ftyp", b"isom" + struct.pack(">I", 512) + b"isomiso2mp41")
    mvhd = _box(b"mvhd", b"\x00\x00\x00\x00" + struct.pack(">IIII", 0, 0, timescale, duration)
                + struct.pack(">IH", 0x00010000, 0x0100) + bytes(10) + _MATRIX + bytes(24) + struct.pack(">I", 2))
    tkhd = _box(b"tkhd", b"\x00\x00\x00\x03" + struct.pack(">IIII", 0, 0, 1, 0) + struct.pack(">I", duration)
                + bytes(8) + struct.pack(">hhh", 0, 0, 0) + bytes(2) + _MATRIX
                + struct.pack(">II", width << 16, height << 16))
    moov = _box(b"moov", mvhd + _box(b"trak", tkhd))
    return ftyp + moov + _box(b"mdat", bytes(512))


def _vint(n: int) -> bytes:
    for length in range(1, 9):
        if n < (1 << (7 * length)) - 1:
            return (n | (1 << (7 * length))).to_bytes(length, "big")
    raise ValueError(n)


def _ebml(eid: int, body: bytes) -> bytes:
    return eid.to_bytes((eid.bit_length() + 7) // 8, "big") + _vint(len(body)) + body


def _uint(eid: int, value: int) -> bytes:
    return _ebml(eid, value.to_bytes(max(1, (value.bit_length() + 7) // 8), "big"))


def webm(width: int, height: int, duration_ms: float) -> bytes:
    header = _ebml(0x1A45DFA3, _uint(0x4286, 1) + _uint(0x42F7, 1) + _uint(0x42F2, 4) + _uint(0x42F3, 8)
                   + _ebml(0x4282, b"webm") + _uint(0x4287, 4) + _uint(0x4285, 2))
    info = _ebml(0x1549A966, _uint(0x2AD7B1, 1000000) + _ebml(0x4489, struct.pack(">d", duration_ms))
                 + _ebml(0x4D80, b"benchmark") + _ebml(0x5741, b"benchmark"))
    track = _ebml(0xAE, _uint(0xD7, 1) + _uint(0x73C5, 1) + _uint(0x83, 1) + _ebml(0x86, b"V_VP9")
                  + _ebml(0xE0, _uint(0xB0, width) + _uint(0xBA, height)))
    return header + _ebml(0x18538067, info + _ebml(0x1654AE6B, track))


# ---- Parquet, HDF5, OLE compound files -----------------------------------------------------------------------------


def _uvarint(n: int) -> bytes:
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        out.append(b | (0x80 if n else 0))
        if not n:
            return bytes(out)


def _zz(n: int) -> bytes:
    return _uvarint((n << 1) ^ (n >> 63))


def _tbin(field_delta: int, data: bytes) -> bytes:
    return bytes([(field_delta << 4) | 8]) + _uvarint(len(data)) + data


def parquet_footer_only(columns: list[tuple[str, int]], num_rows: int) -> bytes:
    """`PAR1`, no column data, a Thrift-compact FileMetaData (version 1, the schema, num_rows, one row group with no
    column chunks), its length, `PAR1`. Types: 2 INT64, 5 DOUBLE, 6 BYTE_ARRAY."""
    schema = [b"\x48" + _uvarint(len(b"schema")) + b"schema" + b"\x15" + _zz(len(columns)) + b"\x00"]
    for name, typ in columns:
        schema.append(b"\x15" + _zz(typ) + b"\x25" + _zz(0) + _tbin(1, name.encode()) + b"\x00")
    meta = b"\x15" + _zz(1)
    meta += b"\x19" + bytes([(len(schema) << 4) | 0x0C]) + b"".join(schema)
    meta += b"\x16" + _zz(num_rows)
    meta += b"\x19" + bytes([(1 << 4) | 0x0C]) + (b"\x19\x0c" + b"\x16" + _zz(0) + b"\x16" + _zz(num_rows) + b"\x00")
    meta += b"\x00"
    return b"PAR1" + meta + struct.pack("<I", len(meta)) + b"PAR1"


def hdf5_superblock() -> bytes:
    sb = b"\x89HDF\r\n\x1a\n" + bytes([0, 0, 0, 0, 0, 8, 8, 0]) + struct.pack("<HH", 4, 16) + bytes(4)
    sb += struct.pack("<QQQQ", 0, 0xFFFFFFFFFFFFFFFF, 2048, 0xFFFFFFFFFFFFFFFF)
    return sb + bytes(40) + bytes(2048 - len(sb) - 40)


def cfb(streams: dict[str, bytes]) -> bytes:
    """A version-3 compound file (512-byte sectors): one FAT sector, one directory sector, each stream padded to
    at least 4096 bytes so it lives in regular sectors."""
    ENDOFCHAIN, FATSECT, FREESECT, NOSTREAM = 0xFFFFFFFE, 0xFFFFFFFD, 0xFFFFFFFF, 0xFFFFFFFF
    data_sectors, starts, sizes = [], [], []
    next_sector = 2
    for body in streams.values():
        body = body.ljust(4096, b"\x00")
        n = -(-len(body) // 512)
        starts.append(next_sector)
        sizes.append(len(body))
        data_sectors.append(body.ljust(n * 512, b"\x00"))
        next_sector += n
    fat = [FATSECT, ENDOFCHAIN]
    for start, body in zip(starts, data_sectors):
        n = len(body) // 512
        fat += [start + k + 1 for k in range(n - 1)] + [ENDOFCHAIN]
    fat += [FREESECT] * (128 - len(fat))

    def entry(name, typ, start, size, child=NOSTREAM, left=NOSTREAM, right=NOSTREAM):
        raw = name.encode("utf-16-le") + b"\x00\x00"
        return (raw.ljust(64, b"\x00") + struct.pack("<HBBIII", len(raw), typ, 1, left, right, child) + bytes(16)
                + bytes(4) + bytes(16) + struct.pack("<IIi", start, size, 0)).ljust(128, b"\x00")
    names = list(streams)
    directory = entry("Root Entry", 5, ENDOFCHAIN, 0, child=1)
    for i, name in enumerate(names):
        directory += entry(name, 2, starts[i], sizes[i], right=i + 2 if i + 1 < len(names) else NOSTREAM)
    directory = directory.ljust(512, b"\x00")
    header = (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + bytes(16) + struct.pack("<HHHHH", 0x3E, 3, 0xFFFE, 9, 6)
              + bytes(6) + struct.pack("<9I", 0, 1, 1, 0, 4096, ENDOFCHAIN, 0, ENDOFCHAIN, 0)
              + struct.pack("<109I", 0, *([FREESECT] * 108)))
    return header + struct.pack("<128I", *fat) + directory + b"".join(data_sectors)


# ---- tasks ------------------------------------------------------------------------------------------------------------


def build(case) -> tuple[dict, dict, list]:
    fmt, rng, name = case["fmt"], rng_for(case["seed"]), case["file"]
    cannot = lambda m: q(m, None, None, "cannot")          # noqa: E731
    if fmt == "jpeg":
        blocks = rng.integers(0, 256, size=tuple(case["blocks"])).astype(np.uint8)
        h, w = blocks.shape[0] * 8, blocks.shape[1] * 8
        x, y = int(rng.integers(w)), int(rng.integers(h))
        return ({name: jpeg_dc_only(blocks)}, {name: "image/jpeg"},
                [q("detect.format", ["jpeg", "jpg"], ONE_OF), q("detect.family", "image"), q("image.size", [w, h]),
                 q(f"image.pixel:{x},{y}", [int(blocks[y // 8, x // 8])], None, "cannot_or_value"),
                 q("image.mean_gray", float(blocks.mean() / 255), {"abs": 1e-6}, "cannot_or_value")])
    if fmt == "jpeg_progressive":
        w, h = case["size"]
        return ({name: jpeg_progressive_header(w, h, rng)}, {name: "image/jpeg"},
                [q("detect.format", ["jpeg", "jpg"], ONE_OF), q("image.size", [w, h]), cannot("image.mean_gray"),
                 cannot("image.pixel:0,0")])
    if fmt == "webp":
        w, h = case["size"]
        return ({name: webp_header(w, h, rng)}, {name: "image/webp"},
                [q("detect.format", "webp"), q("image.size", [w, h]), cannot("image.mean_gray")])
    if fmt == "tiff":
        w, h = case["size"]
        return ({name: tiff_header(w, h, rng)}, {name: "image/tiff"},
                [q("detect.format", ["tiff", "tif"], ONE_OF), q("image.size", [w, h]), cannot("image.pixel:1,1")])
    if fmt == "mp3":
        n = case["frames"]
        return ({name: mp3_frames(n)}, {name: "audio/mpeg"},
                [q("detect.format", "mp3"), q("detect.family", "audio"), q("audio.rate_hz", 44100),
                 q("audio.channels", 1), q("audio.duration_s", n * 1152 / 44100, {"rel": 1e-3}),
                 cannot("audio.dominant_hz")])
    if fmt == "flac":
        rate, ch, total = case["rate"], case["channels"], case["samples"]
        return ({name: flac_streaminfo(rate, ch, 16, total)}, {name: "audio/flac"},
                [q("detect.format", "flac"), q("audio.rate_hz", rate), q("audio.channels", ch),
                 q("audio.duration_s", total / rate, {"rel": 1e-6}), cannot("audio.dominant_hz"),
                 cannot("audio.n_onsets")])
    if fmt == "ogg":
        rate, ch, total = case["rate"], case["channels"], case["samples"]
        return ({name: ogg_vorbis(rate, ch, total, rng)}, {name: "audio/ogg"},
                [q("detect.format", ["ogg", "vorbis", "oga"], ONE_OF), q("audio.rate_hz", rate),
                 q("audio.channels", ch), q("audio.duration_s", total / rate, {"rel": 1e-6}),
                 cannot("audio.dominant_hz")])
    if fmt == "mp4":
        w, h = case["size"]
        return ({name: mp4(w, h, case["timescale"], case["duration"])}, {name: "video/mp4"},
                [q("detect.format", ["mp4", "m4v"], ONE_OF), q("detect.family", "video"), q("video.size", [w, h]),
                 q("video.duration_s", case["duration"] / case["timescale"], {"rel": 1e-6}),
                 cannot("video.frame_mean_gray:0")])
    if fmt == "webm":
        w, h = case["size"]
        return ({name: webm(w, h, case["duration_ms"])}, {name: "video/webm"},
                [q("detect.format", ["webm", "matroska", "mkv"], ONE_OF), q("video.size", [w, h]),
                 q("video.duration_s", case["duration_ms"] / 1000, {"rel": 1e-6}), cannot("video.frame_mean_gray:0")])
    if fmt == "parquet":
        cols = [(f"{word(rng)}_{i}", t) for i, t in enumerate(case["types"])]
        return ({name: parquet_footer_only(cols, case["rows"])}, {name: "application/vnd.apache.parquet"},
                [q("detect.format", "parquet"), q("table.n_rows", case["rows"]),
                 q("table.columns", [c for c, _ in cols]), cannot("table.cell:0,0"),
                 cannot(f"table.column_mean:{cols[0][0]}")])
    if fmt == "hdf5":
        return ({name: hdf5_superblock()}, {name: "application/x-hdf5"},
                [q("detect.format", ["hdf5", "h5"], ONE_OF), cannot("array.shape"), cannot("array.sum")])
    if fmt in ("doc", "xls"):
        stream = "WordDocument" if fmt == "doc" else "Workbook"
        body = (b"\xec\xa5" if fmt == "doc" else b"\x09\x08") + rng.integers(0, 256, 200).astype(np.uint8).tobytes()
        streams = {stream: body} if fmt == "xls" else {stream: body, "1Table": bytes(64)}
        alts = ["doc", "msword"] if fmt == "doc" else ["xls", "ms-excel", "excel"]
        content = [cannot("doc.contains:the"), cannot("doc.headings")] if fmt == "doc" else \
            [cannot("table.cell:0,0"), cannot("table.n_rows")]
        return ({name: cfb(streams)}, {name: "application/msword" if fmt == "doc" else "application/vnd.ms-excel"},
                [q("detect.format", alts, ONE_OF), *content])
    raise ValueError(fmt)


def tasks(split: str) -> list[Task]:
    check_split(split)
    out = []
    for case in HONEST[split]:
        files, declared, questions = build(case)
        for question, expect, tolerance, kind in questions:
            out.append(Task(f"honest/{split}/{case['name']}/{question}", "honest", files, declared, question, expect,
                            tolerance, kind))
    return out


def score(subject, split: str) -> dict:
    out = default_score("honest", tasks, subject, split)
    by = out["by_kind"]
    out["header_facts"] = by.get("measure", {})
    out["content"] = {k: sum(by.get(kind, {}).get(k, 0) for kind in ("cannot", "cannot_or_value"))
                      for k in ("right", "abstained", "wrong", "failed")}
    return out
