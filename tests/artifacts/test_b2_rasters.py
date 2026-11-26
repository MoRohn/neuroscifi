"""NetPBM, PAM, farbfeld, QOI, BMP (incl. RLE), ICO and GIF (lane B2): reference pixels recovered from files built by
independent encoders written here; hostile variants are named errors."""
import random
import struct

import numpy as np
import pytest

from b2_fixtures import png
from neuroscifi.artifacts import adapters
from neuroscifi.artifacts import types as T
from neuroscifi.artifacts.adapters import _b2

_b2.register_all()


def _rand(h, w, ch, top, seed):
    return np.random.default_rng(seed).integers(0, top + 1, (h, w, ch))


# ------------------------------------------------------------------------------------------------------------ NetPBM


def test_netpbm_plain_and_raw_forms():
    img = _rand(3, 4, 3, 255, 1)
    p3 = "P3\n# comment\n4 3\n255\n" + " ".join(str(v) for v in img.ravel())
    p6 = b"P6 4 3 255\n" + img.astype(np.uint8).tobytes()
    for data in (p3.encode(), p6):
        out = adapters.decode(data)
        assert out.detected.format == "ppm" and (out.representation.pixels == img).all()
    g = _rand(2, 5, 1, 65535, 2)[:, :, 0]
    out = adapters.decode(b"P5\n5 2\n65535\n" + g.astype(">u2").tobytes())
    assert out.representation.bits == 16 and (out.representation.pixels == g).all()
    out = adapters.decode(b"P2 2 1 100\n0 100\n")
    assert out.representation.pixels.tolist() == [[0, 255]] and any("rescaled" in n for n in out.notes)


def test_pbm_plain_and_raw_with_black_as_zero():
    out = adapters.decode(b"P1\n3 2\n1 0 1\n0 0 1\n")
    assert out.representation.pixels.tolist() == [[0, 1, 0], [1, 1, 0]] and out.representation.bits == 1
    out = adapters.decode(b"P4\n10 1\n" + bytes([0b10100000, 0b01000000]))
    assert out.representation.pixels[0].tolist() == [0, 1, 0, 1, 1, 1, 1, 1, 1, 0]


def test_pam_rgba_and_hostile_netpbm():
    body = bytes([1, 2, 3, 4, 5, 6, 7, 8])
    out = adapters.decode(b"P7\nWIDTH 2\nHEIGHT 1\nDEPTH 4\nMAXVAL 255\nTUPLTYPE RGB_ALPHA\nENDHDR\n" + body)
    assert out.representation.mode == "rgba" and out.representation.pixels.ravel().tolist() == list(body)
    with pytest.raises(T.Malformed):
        adapters.decode(b"P6 4 3 255\n" + b"\x00" * 10)
    with pytest.raises(T.Malformed):
        adapters.decode(b"P2 2 1 10\n0 11\n")
    with pytest.raises(T.Refused):
        adapters.decode(b"P6 99999 99999 255\n\x00")


def test_farbfeld():
    px = _rand(2, 3, 4, 65535, 3)
    out = adapters.decode(b"farbfeld" + struct.pack(">II", 3, 2) + px.astype(">u2").tobytes())
    assert out.representation.bits == 16 and (out.representation.pixels == px).all()


# --------------------------------------------------------------------------------------------------------------- QOI


def _qoi_encode(px, channels):
    """Reference QOI encoder (the published algorithm), written independently of the decoder."""
    h, w, _ = px.shape
    out = bytearray(b"qoif" + struct.pack(">IIBB", w, h, channels, 0))
    index = [(0, 0, 0, 0)] * 64
    prev = (0, 0, 0, 255)
    run = 0
    flat = [tuple(int(v) for v in p) + ((255,) if channels == 3 else ()) for p in px.reshape(-1, px.shape[2])]
    for i, p in enumerate(flat):
        if p == prev:
            run += 1
            if run == 62 or i == len(flat) - 1:
                out.append(0xC0 | (run - 1))
                run = 0
            continue
        if run:
            out.append(0xC0 | (run - 1))
            run = 0
        h_ = (p[0] * 3 + p[1] * 5 + p[2] * 7 + p[3] * 11) % 64
        if index[h_] == p:
            out.append(h_)
        else:
            index[h_] = p
            if p[3] == prev[3]:
                vr, vg, vb = ((p[k] - prev[k] + 128) % 256 - 128 for k in range(3))
                vgr, vgb = vr - vg, vb - vg
                if -2 <= vr <= 1 and -2 <= vg <= 1 and -2 <= vb <= 1:
                    out.append(0x40 | (vr + 2) << 4 | (vg + 2) << 2 | (vb + 2))
                elif -32 <= vg <= 31 and -8 <= vgr <= 7 and -8 <= vgb <= 7:
                    out += bytes((0x80 | (vg + 32), (vgr + 8) << 4 | (vgb + 8)))
                else:
                    out += bytes((0xFE, p[0], p[1], p[2]))
            else:
                out += bytes((0xFF,) + p)
        prev = p
    return bytes(out) + b"\x00" * 7 + b"\x01"


@pytest.mark.parametrize("channels", [3, 4])
def test_qoi_all_operations(channels):
    rng = np.random.default_rng(channels)
    px = rng.integers(0, 256, (16, 16, channels)).astype(np.uint8)
    px[2:4] = px[1, 0]                                    # runs
    px[5, :, :3] = (px[4, :, :3].astype(int) + 1) % 256    # small diffs
    px[6, :8] = px[0, :8]                                  # index hits
    data = _qoi_encode(px, channels)
    out = adapters.decode(data)
    assert out.detected.format == "qoi" and (out.representation.pixels == px).all()
    with pytest.raises(T.ArtifactError):
        adapters.decode(data[:-9] + b"\x00" * 7 + b"\x01")


# --------------------------------------------------------------------------------------------------------------- BMP


def _bmp(w, h, bpp, rows, palette=None, comp=0, top_down=False, hsize=40, masks=None):
    """rows: bytes of the pixel array exactly as stored (already padded, in stored order)."""
    pal = b"".join(bytes((b, g, r, 0)) for r, g, b in palette) if palette else b""
    extra = b""
    if hsize >= 108:
        info = struct.pack("<IiiHHIIiiII", hsize, w, -h if top_down else h, 1, bpp, comp, len(rows), 0, 0,
                           len(palette or []), 0)
        info += struct.pack("<4I", *(masks or (0, 0, 0, 0))) + b"\x00" * (hsize - 56)
    else:
        info = struct.pack("<IiiHHIIiiII", 40, w, -h if top_down else h, 1, bpp, comp, len(rows), 0, 0,
                           len(palette or []), 0)
        if masks:
            extra = struct.pack("<3I", *masks[:3])
    offset = 14 + len(info) + len(extra) + len(pal)
    return b"BM" + struct.pack("<IHHI", offset + len(rows), 0, 0, offset) + info + extra + pal + rows


def _pad(row):
    return row + b"\x00" * (-len(row) % 4)


def test_bmp_24bit_bottom_up_and_8bit_top_down():
    img = _rand(3, 5, 3, 255, 4).astype(np.uint8)
    rows = b"".join(_pad(img[y, :, ::-1].tobytes()) for y in reversed(range(3)))
    out = adapters.decode(_bmp(5, 3, 24, rows))
    assert out.detected.format == "bmp" and (out.representation.pixels == img).all()
    palette = [(10, 20, 30), (40, 50, 60), (70, 80, 90)]
    idx = np.array([[0, 1, 2], [2, 1, 0]])
    rows = b"".join(_pad(bytes(r)) for r in idx.tolist())
    out = adapters.decode(_bmp(3, 2, 8, rows, palette, top_down=True))
    assert out.representation.pixels.tolist() == [[list(palette[i]) for i in r] for r in idx.tolist()]


def test_bmp_1_and_4_bit():
    palette = [(0, 0, 0), (255, 255, 255)]
    rows = _pad(bytes([0b10110000]))
    out = adapters.decode(_bmp(4, 1, 1, rows, palette))
    assert out.representation.pixels[0, :, 0].tolist() == [255, 0, 255, 255]
    palette16 = [(i * 16, i, 0) for i in range(16)]
    rows = _pad(bytes([0x1F, 0x30]))
    out = adapters.decode(_bmp(3, 1, 4, rows, palette16))
    assert out.representation.pixels[0, :, 0].tolist() == [16, 240, 48]


def test_bmp_16bit_565_bitfields_and_32bit_v5_alpha():
    px = [0xF800, 0x07E0, 0x001F]
    rows = _pad(struct.pack("<3H", *px))
    out = adapters.decode(_bmp(3, 1, 16, rows, comp=3, masks=(0xF800, 0x07E0, 0x001F)))
    assert out.representation.pixels[0].tolist() == [[255, 0, 0], [0, 255, 0], [0, 0, 255]]
    rows = struct.pack("<2I", 0x80FF0000, 0x0000FF00)
    out = adapters.decode(_bmp(2, 1, 32, rows, comp=3, hsize=124,
                               masks=(0x00FF0000, 0x0000FF00, 0x000000FF, 0xFF000000)))
    assert out.representation.mode == "rgba"
    assert out.representation.pixels[0].tolist() == [[255, 0, 0, 128], [0, 255, 0, 0]]
    out = adapters.decode(_bmp(2, 1, 32, struct.pack("<2I", 0xFF0000, 0xFF)))
    assert out.representation.mode == "rgb" and out.representation.pixels[0].tolist() == [[255, 0, 0], [0, 0, 255]]


def test_bmp_rle8_and_rle4_are_decoded():
    palette = [(0, 0, 0), (255, 0, 0), (0, 255, 0), (0, 0, 255)]
    # bottom-up, 4 x 2: row 0 (bottom) = run of 3 x index1 + absolute [2]; row 1: delta then 1 pixel; end
    rle = bytes([3, 1, 0, 0]) + bytes([0, 2, 2, 0]) + bytes([1, 3, 0, 1])
    out = adapters.decode(_bmp(4, 2, 8, rle, palette, comp=1))
    p = out.representation.pixels
    assert out.representation.mode == "rgba"
    assert p[1, :3, :3].tolist() == [[255, 0, 0]] * 3 and p[1, 3, 3] == 0      # bottom row; x=3 never written
    assert p[0, 2, :3].tolist() == [0, 0, 255] and p[0, 0, 3] == 0              # top row after a delta of 2
    rle4 = bytes([4, 0x12, 0, 1])
    out = adapters.decode(_bmp(4, 1, 4, rle4, palette + [(9, 9, 9)] * 12, comp=2))
    assert out.representation.pixels[0, :, 0].tolist() == [255, 0, 255, 0]


def test_bmp_hostile():
    with pytest.raises(T.Malformed):
        adapters.decode(_bmp(5, 3, 24, b"\x00" * 10))
    with pytest.raises(T.Malformed):
        adapters.decode(_bmp(2, 1, 8, _pad(bytes([0, 7])), [(0, 0, 0)]))


# --------------------------------------------------------------------------------------------------------------- ICO


def test_ico_with_png_and_bmp_entries_decodes_the_largest():
    small = _bmp(2, 2, 32, struct.pack("<4I", *([0xFF00FF00] * 4)))[14:]      # headerless DIB
    small = struct.pack("<I", 40) + struct.pack("<i", 2) + struct.pack("<i", 4) + small[12:]   # height doubled
    small += _pad(b"\x00") * 2                                                   # AND mask rows
    big = png([[(1, 2, 3)] * 4] * 4)
    entries = [(2, 2, small), (4, 4, big)]
    offset = 6 + 16 * len(entries)
    head = struct.pack("<HHH", 0, 1, len(entries))
    body = b""
    for w, h, blob in entries:
        head += struct.pack("<BBBBHHII", w, h, 0, 0, 1, 32, len(blob), offset + len(body))
        body += blob
    out = adapters.decode(head + body)
    assert out.detected.format == "ico" and out.representation.size == (4, 4)
    assert "2x2, 4x4" in out.notes[0]


# --------------------------------------------------------------------------------------------------------------- GIF


def _lzw_encode(indices, min_size):
    """Reference GIF LZW encoder (dictionary of strings, LSB-first packing, growing code width)."""
    clear, end = 1 << min_size, (1 << min_size) + 1
    table = {bytes((i,)): i for i in range(clear)}
    nxt, width = end + 1, min_size + 1
    codes = [(clear, width)]
    w = b""
    for k in indices:
        wk = w + bytes((k,))
        if wk in table:
            w = wk
            continue
        codes.append((table[w], width))
        if nxt < 4096:
            table[wk] = nxt
            nxt += 1
            if nxt > (1 << width) and width < 12:
                width += 1
        else:
            codes.append((clear, width))
            table = {bytes((i,)): i for i in range(clear)}
            nxt, width = end + 1, min_size + 1
        w = bytes((k,))
    if w:
        codes.append((table[w], width))
    codes.append((end, width))
    acc = nbits = 0
    out = bytearray()
    for code, cw in codes:
        acc |= code << nbits
        nbits += cw
        while nbits >= 8:
            out.append(acc & 0xFF)
            acc >>= 8
            nbits -= 8
    if nbits:
        out.append(acc)
    return bytes(out)


def _blocks(data):
    return b"".join(bytes((len(data[i:i + 255]),)) + data[i:i + 255] for i in range(0, len(data), 255)) + b"\x00"


def _gif(w, h, frames, palette, loops=None, comment=None):
    """frames: list of (x, y, fw, fh, indices list, delay_cs, dispose, transparent or None, interlace)."""
    size_bits = max(1, (len(palette) - 1).bit_length()) - 1
    pal = b"".join(bytes(c) for c in palette) + b"\x00" * (3 * (2 << size_bits) - 3 * len(palette))
    out = b"GIF89a" + struct.pack("<HHBBB", w, h, 0x80 | size_bits, 0, 0) + pal
    if loops is not None:
        out += b"\x21\xff" + _blocks(b"NETSCAPE2.0" + b"\x01" + struct.pack("<H", loops))
    if comment:
        out += b"\x21\xfe" + _blocks(comment)
    for x, y, fw, fh, idx, delay, dispose, transp, interlace in frames:
        flags = (dispose << 2) | (1 if transp is not None else 0)
        out += b"\x21\xf9\x04" + struct.pack("<BHB", flags, delay, transp or 0) + b"\x00"
        rows = [idx[r * fw:(r + 1) * fw] for r in range(fh)]
        if interlace:
            order = list(range(0, fh, 8)) + list(range(4, fh, 8)) + list(range(2, fh, 4)) + list(range(1, fh, 2))
            rows = [rows[r] for r in order]
        stored = [v for r in rows for v in r]
        min_size = max(2, size_bits + 1)
        out += b"\x2c" + struct.pack("<HHHHB", x, y, fw, fh, 0x40 if interlace else 0)
        out += bytes((min_size,)) + _blocks(_lzw_encode(stored, min_size))
    return out + b"\x3b"


def test_gif_single_frame_long_lzw_stream_with_interlace():
    rnd = random.Random(5)
    palette = [(i, 255 - i, i // 2) for i in range(0, 256, 16)]     # 16 colours
    w, h = 37, 29
    idx = [rnd.randrange(16) if rnd.random() < 0.3 else 3 for _ in range(w * h)]
    out = adapters.decode(_gif(w, h, [(0, 0, w, h, idx, 0, 0, None, True)], palette, comment=b"made here"))
    r = out.representation
    assert out.detected.format == "gif" and r.mode == "rgb" and r.text() == "made here"
    assert r.pixels.reshape(-1, 3).tolist() == [list(palette[i]) for i in idx]


def test_gif_table_growth_to_12_bits_and_reset():
    rnd = random.Random(9)
    palette = [(i, i, i) for i in range(256)]
    idx = [rnd.randrange(256) for _ in range(200 * 60)]               # forces codes past 4095 and a clear
    out = adapters.decode(_gif(200, 60, [(0, 0, 200, 60, idx, 0, 0, None, False)], palette))
    assert out.representation.pixels[:, :, 0].ravel().tolist() == idx


def test_gif_animation_disposal_transparency_and_delays():
    palette = [(0, 0, 0), (255, 0, 0), (0, 0, 255), (9, 9, 9)]
    f0 = (0, 0, 2, 2, [1, 1, 1, 1], 10, 1, None, False)          # red everywhere, keep
    f1 = (1, 1, 1, 1, [2], 20, 3, None, False)                    # blue at (1,1), restore previous
    f2 = (0, 0, 2, 1, [3, 2], 5, 2, 3, False)                     # index 3 transparent: (0,0) stays red
    out = adapters.decode(_gif(2, 2, [f0, f1, f2], palette, loops=0))
    r = out.representation
    assert isinstance(r, T.Frames) and r.delays_s == (0.1, 0.2, 0.05)
    assert r.frames[1].pixels[1, 1].tolist() == [0, 0, 255, 255]
    assert r.frames[2].pixels[1, 1].tolist() == [255, 0, 0, 255]  # frame 1 restored to previous
    assert r.frames[2].pixels[0, 0].tolist() == [255, 0, 0, 255] and r.frames[2].pixels[0, 1].tolist() == [0, 0, 255, 255]
    assert any("endlessly" in n for n in out.notes)


def test_gif_hostile():
    palette = [(0, 0, 0), (1, 1, 1)]
    good = _gif(4, 4, [(0, 0, 4, 4, [1] * 16, 0, 0, None, False)], palette)
    with pytest.raises(T.ArtifactError):
        adapters.decode(good[:20])
    with pytest.raises(T.Refused):
        adapters.decode(_gif(4, 4, [(0, 0, 4, 4, [1] * 16, 0, 0, None, False)] * 5, palette),
                        limits=T.Limits(max_members=3))
    rnd = random.Random(3)
    for _ in range(200):
        bad = bytearray(good)
        bad[rnd.randrange(13, len(bad))] = rnd.randrange(256)
        try:
            adapters.decode(bytes(bad))
        except T.ArtifactError:
            pass
