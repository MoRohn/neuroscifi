"""Lane G2: raster images (make/raster.py, make/png_write.py) and the independent PNG/APNG check (check/png.py).

The reader is tested against PNG files assembled here by hand (struct + zlib), one per scanline filter; wrong
artifacts are re-encoded here with this file's own encoder."""
import struct
import zlib

import numpy as np
import pytest

from neuroscifi.artifacts import check, make
from neuroscifi.artifacts.check import _g2 as _checks  # noqa: F401
from neuroscifi.artifacts.check import png as pngcheck
from neuroscifi.artifacts.make import _g2  # noqa: F401
from neuroscifi.artifacts.spec import Spec

GRID = [[0, 1, 2, 3], [4, None, 6, 7], [8, 9, 10.5, 11]]


def _spec(form, data, options=None):
    return {"spec": form, "version": 1, "title": "A grid", "description": "Made for a test.", "data": data,
            "options": options or {}, "sources": ["request"], "made_by": {"generator": "", "version": 0}}


def _failed(verdict):
    return {f.rule for f in verdict.technical if not f.passed}


def _chunk(kind, body):
    return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))


def _encode(pixels, *, color, depth=8, filters=None, extra=b"", interlace=0):
    """This test's own PNG encoder, with a chosen filter per row (filters applied here, by the book)."""
    h, w = pixels.shape[:2]
    channels = {0: 1, 2: 3, 4: 2, 6: 4}[color]
    bpp = channels * depth // 8
    raw = pixels.astype(">u2" if depth == 16 else "u1").reshape(h, -1).view(np.uint8).astype(np.int64)
    out = bytearray()
    prior = np.zeros(raw.shape[1], dtype=np.int64)
    for y in range(h):
        kind = (filters or [0] * h)[y]
        line = raw[y]
        enc = np.zeros_like(line)
        for i in range(len(line)):
            a = line[i - bpp] if i >= bpp else 0
            b = prior[i]
            c = prior[i - bpp] if i >= bpp else 0
            if kind == 0:
                pred = 0
            elif kind == 1:
                pred = a
            elif kind == 2:
                pred = b
            elif kind == 3:
                pred = (a + b) // 2
            else:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pred = a if pa <= pb and pa <= pc else (b if pb <= pc else c)
            enc[i] = (line[i] - pred) & 0xFF
        out.append(kind)
        out.extend(bytes(enc.astype(np.uint8)))
        prior = line
    ihdr = struct.pack(">IIBBBBB", w, h, depth, color, 0, 0, interlace)
    return (b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", ihdr) + extra + _chunk(b"IDAT", zlib.compress(bytes(out)))
            + _chunk(b"IEND", b""))


@pytest.mark.parametrize("kind", [0, 1, 2, 3, 4])
def test_reader_undoes_every_filter_of_a_hand_built_png(kind):
    rng = np.random.default_rng(kind)
    pixels = rng.integers(0, 256, size=(5, 7, 3), dtype=np.uint8)
    p = pngcheck.read(_encode(pixels, color=2, filters=[kind] * 5))
    assert p.decoded and not p.problems
    assert (p.image == pixels).all()
    mixed = rng.integers(0, 65536, size=(4, 3, 2), dtype=np.uint16)
    p = pngcheck.read(_encode(mixed, color=4, depth=16, filters=[4, 3, 1, 2]))
    assert (p.image == mixed).all()


@pytest.mark.parametrize("options", [{}, {"bits": 16}, {"ramp": "light_low", "warn_above": 9},
                                     {"warn_below": 1, "bits": 16, "range": [0, 10]}, {"cell_px": 1}])
def test_raster_made_and_every_value_recovered(options):
    made = make.make(_spec("image.raster", {"grid": GRID}, options))
    assert made.media_type == "image/png"
    verdict = check.check(made.bytes, made.media_type, made.spec)
    assert verdict.ok, [(f.rule, f.detail) for f in verdict.technical if not f.passed]
    assert next(f for f in verdict.technical if f.rule == "values_recovered").measure == 1.0
    assert made.bytes == make.make(_spec("image.raster", {"grid": GRID}, options)).bytes
    assert "Gray scale" in pngcheck.read(made.bytes).texts["Description"]


def test_animation_made_and_checked():
    frames = [[[(i + j + k) % 5 for j in range(8)] for i in range(6)] for k in range(10)]
    made = make.make(_spec("image.apng", {"frames": frames, "delays_ms": [100]}, {"warn_above": 3}))
    assert made.media_type == "image/apng"
    verdict = check.check(made.bytes, made.media_type, made.spec)
    assert verdict.ok, _failed(verdict)
    p = pngcheck.read(made.bytes)
    assert p.animated and p.num_frames == 10 and [f.delay_s for f in p.frames] == [0.1] * 10


def test_a_changed_value_is_caught_with_its_share():
    made = make.make(_spec("image.raster", {"grid": GRID}))
    changed = Spec.from_dict(_spec("image.raster", {"grid": [[0, 1, 2, 3], [4, None, 6, 7], [8, 9, 9.5, 11]]}))
    verdict = check.check(made.bytes, "image/png", changed)
    rec = next(f for f in verdict.technical if f.rule == "values_recovered")
    assert not rec.passed and rec.measure == pytest.approx(10 / 11)


def test_a_green_fill_and_an_opaque_missing_cell_are_caught():
    made = make.make(_spec("image.raster", {"grid": GRID}, {"warn_above": 9}))
    image = pngcheck.read(made.bytes).image.copy()        # RGBA: warnings and a missing value
    image[0:10, 0:10] = (0, 200, 0, 255)                  # green over a cell the spec does not flag
    image[128:256, 128:256, 3] = 255                      # the missing cell made opaque
    verdict = check.check(_encode(image, color=6), "image/png", made.spec)
    assert {"hue_only_on_warnings", "missing_marked"} <= _failed(verdict)


def test_a_flagged_cell_without_its_outline_is_caught():
    made = make.make(_spec("image.raster", {"grid": [[0, 5], [10, 2]]}, {"warn_above": 9}))
    image = pngcheck.read(made.bytes).image.copy()
    centre = image[image.shape[0] * 3 // 4, image.shape[1] // 4, 0]
    image[image.shape[0] // 2:, : image.shape[1] // 2, :3] = centre    # paint over the outline of the cell holding 10
    verdict = check.check(_encode(image, color=2), "image/png", made.spec)
    assert "hue_only_on_warnings" in _failed(verdict)


def test_wrong_delays_and_frame_count_are_caught():
    frames = [[[k, k + 1], [k + 2, k + 3]] for k in range(4)]
    made = make.make(_spec("image.apng", {"frames": frames, "delays_ms": [100, 100, 100, 100]}))
    slower = Spec.from_dict(_spec("image.apng", {"frames": frames, "delays_ms": [100, 200, 100, 100]}))
    assert "delays" in _failed(check.check(made.bytes, "image/apng", slower))
    more = Spec.from_dict(_spec("image.apng", {"frames": frames + frames[:1], "delays_ms": [100]}))
    assert "frame_count" in _failed(check.check(made.bytes, "image/apng", more))


def test_a_bad_crc_truncation_and_trailing_bytes_are_caught():
    made = make.make(_spec("image.raster", {"grid": GRID}))
    corrupt = bytearray(made.bytes)
    corrupt[40] ^= 0xFF
    assert "crc" in _failed(check.check(bytes(corrupt), "image/png", made.spec))
    assert "png_structure" in _failed(check.check(made.bytes[:-20], "image/png", made.spec))
    assert "png_structure" in _failed(check.check(made.bytes + b"junk", "image/png", made.spec))


def test_sequence_gap_in_an_animation_is_caught():
    frames = [[[k, k + 1], [k + 2, k + 3]] for k in range(3)]
    made = make.make(_spec("image.apng", {"frames": frames, "delays_ms": [50]}))
    data = bytearray(made.bytes)
    at = data.find(b"fdAT")
    data[at + 4:at + 8] = struct.pack(">I", 9)
    body_len = struct.unpack(">I", bytes(data[at - 4:at]))[0]
    data[at + 4 + body_len:at + 8 + body_len] = struct.pack(">I", zlib.crc32(bytes(data[at:at + 4 + body_len])))
    verdict = check.check(bytes(data), "image/apng", made.spec)
    assert "png_structure" in _failed(verdict)


@pytest.mark.parametrize("bad", [
    b"", b"\x89PNG\r\n\x1a\n", b"GIF89a" + b"\x00" * 40,
    b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", struct.pack(">IIBBBBB", 100000, 100000, 16, 6, 0, 0, 0))
    + _chunk(b"IDAT", zlib.compress(b"\x00" * 1000)) + _chunk(b"IEND", b""),
    b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 0, 0, 0, 0))
    + _chunk(b"IDAT", zlib.compress(b"\x00" * 10_000_000)) + _chunk(b"IEND", b""),
    b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 0, 0, 0, 0))
    + _chunk(b"XXXX", b"") + _chunk(b"IEND", b""),
    b"\x89PNG\r\n\x1a\n" + _chunk(b"IDAT", b"") + _chunk(b"IEND", b""),
    b"\x89PNG\r\n\x1a\n" + b"\xff\xff\xff\xffIHDR",
])
def test_hostile_png_bytes_are_named_findings(bad):
    verdict = check.check(bad, "image/png")
    assert not verdict.ok


def test_unread_kinds_are_reported_not_guessed():
    pixels = np.zeros((2, 2), dtype=np.uint8)
    p = pngcheck.read(_encode(pixels, color=0, interlace=1))
    assert not p.decoded and any("not read here" in x for x in p.problems)
    verdict = check.check(_encode(pixels, color=0, interlace=1), "image/png")
    assert "pixels_decoded" in _failed(verdict) and "png_structure" not in _failed(verdict)
