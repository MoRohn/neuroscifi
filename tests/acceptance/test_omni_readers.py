"""The OMNIMIND benchmark's readers and encoders against hand-made references (lane Z).

Readers (acceptance/omni/readers.py) must recover what a right artifact carries and catch each deliberately WRONG
one: a chart whose marks sit 10 % off their scale, a bar of the wrong height, a tone at the wrong pitch, a mesh
with an inverted face or a flipped normal, an APNG with a wrong delay, an SVG with a script or a hue. References are
written out in this file (SVG text, struct-packed STL, PNG chunks around zlib.compress), not with the benchmark's
encoders. Encoders (enc.py) are checked against the standard library's decoders and a byte-exact known GIF.
"""
import gzip
import io
import math
import struct
import tarfile
import wave
import zipfile
import zlib

import numpy as np
import pytest

from neuroscifi.acceptance.omni import enc, readers

# ---- SVG charts -------------------------------------------------------------------------------------------------------


def line_chart(values, *, skew=1.0, extra="", hue_mark=None) -> bytes:
    """x ticks 0..4 at 50 + 100 x; y ticks 0, 10, 20 at 250, 150, 50 (10 per 100 px, upward). `skew` misplaces the
    marks: a value v is drawn where v * skew belongs."""
    y = lambda v: 250 - 10 * v * skew          # noqa: E731
    pts = " ".join(f"{50 + 100 * i},{y(v)}" for i, v in enumerate(values))
    circles = "".join(f'<circle class="mark" cx="{50 + 100 * i}" cy="{y(v)}" r="2"'
                      + (' fill="#d22"' if hue_mark == i else "") + "/>" for i, v in enumerate(values))
    xt = "".join(f'<g class="tick" transform="translate({50 + 100 * i},0)"><line y2="4"/><text>{i}</text></g>'
                 for i in range(len(values)))
    yt = "".join(f'<g class="tick" transform="translate(0,{250 - 10 * v})"><text>{v}</text></g>' for v in (0, 10, 20))
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="500" height="300">{extra}'
            f'<g class="axis x" transform="translate(0,260)">{xt}</g><g class="axis y" transform="translate(40,0)">'
            f'{yt}</g><g class="plot"><g class="series" data-name="flow"><polyline class="series" points="{pts}" '
            f'fill="none" stroke="currentColor"/>{circles}</g></g>'
            '<metadata>{"values": [999, 999]}</metadata></svg>').encode()


def test_line_chart_values_recovered_through_the_scales():
    values = [3.0, 7.5, 12.0, 18.25, 4.0]
    r = readers.read_chart(line_chart(values))
    assert r.x.kind == "linear" and r.y.kind == "linear"
    line = [m for m in r.marks if m.kind == "line"][0]
    assert line.series == "flow"
    assert [round(x, 6) for x, _ in line.points] == [0, 1, 2, 3, 4]
    assert [round(v, 6) for _, v in line.points] == values
    pts = [(m.x, m.y) for m in r.marks if m.kind == "point"]
    src = list(enumerate(values))
    assert readers.carried_points(src, pts, 0.05, 0.2, ordered=False) == 5


def test_a_chart_whose_marks_are_ten_percent_off_its_scale_is_caught():
    values = [3.0, 7.5, 12.0, 18.25, 4.0]
    r = readers.read_chart(line_chart(values, skew=1.1))
    line = [m for m in r.marks if m.kind == "line"][0]
    tol = 0.01 * 20                    # 1 % of the axis span
    assert readers.carried_points(list(enumerate(values)), list(line.points), 0.05, tol, ordered=True) == 0


def test_metadata_is_never_read():
    r = readers.read_chart(line_chart([1.0, 2.0]))
    assert all(999 not in (m.x, m.y) for m in r.marks)


def test_bar_chart_categories_and_a_wrong_bar():
    def bars(heights):
        rects = "".join(f'<rect class="mark" x="{60 + 80 * i}" y="{200 - 4 * h}" width="40" height="{4 * h}"/>'
                        for i, h in enumerate(heights))
        xt = "".join(f'<g class="tick" transform="translate({80 + 80 * i},210)"><text>{c}</text></g>'
                     for i, c in enumerate(("north", "south", "east")))
        yt = "".join(f'<g class="tick" transform="translate(50,{200 - 4 * v})"><text>{v}</text></g>' for v in (0, 25, 50))
        return (f'<svg xmlns="http://www.w3.org/2000/svg"><g class="axis x">{xt}</g><g class="axis y">{yt}</g>'
                f'<g class="plot" transform="translate(0,0)"><g class="series" data-name="n">{rects}</g></g></svg>'
                ).encode()
    r = readers.read_chart(bars([12, 40, 7]))
    got = {m.x: round(m.y, 6) for m in r.marks}
    assert got == {"north": 12, "south": 40, "east": 7}
    wrong = readers.read_chart(bars([12, 44, 7]))
    assert {m.x: round(m.y, 6) for m in wrong.marks}["south"] != 40


def test_log_scale_scatter_and_negative_bars():
    yt = "".join(f'<g class="tick" transform="translate(0,{300 - 100 * k})"><text>{10 ** k}</text></g>'
                 for k in range(3))
    xt = "".join(f'<g class="tick" transform="translate({100 * k},0)"><text>{k * 5}</text></g>' for k in range(3))
    pts = [(2.5, 3.0), (7.0, 50.0)]
    circles = "".join(f'<circle class="mark" cx="{x * 20}" cy="{300 - 100 * math.log10(y)}"/>' for x, y in pts)
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg"><g class="axis x">{xt}</g><g class="axis y">{yt}</g>'
           f'<g class="plot">{circles}</g></svg>').encode()
    r = readers.read_chart(svg)
    assert r.y.kind == "log"
    assert [(round(m.x, 6), round(m.y, 6)) for m in r.marks] == pts
    # a bar below zero: its value is the edge away from the zero line
    yt = "".join(f'<g class="tick" transform="translate(0,{100 - 10 * v})"><text>{v}</text></g>' for v in (-5, 0, 5))
    neg = (f'<svg xmlns="http://www.w3.org/2000/svg"><g class="axis x"><g class="tick" transform="translate(10,0)">'
           f'<text>a</text></g></g><g class="axis y">{yt}</g><g class="plot"><rect class="mark" x="5" y="100" '
           'width="10" height="30"/></g></svg>').encode()
    assert readers.read_chart(neg).marks[0].y == pytest.approx(-3.0)


def test_histogram_bins_and_dates():
    xt = "".join(f'<g class="tick" transform="translate({40 * k},0)"><text>{k}</text></g>' for k in range(5))
    yt = "".join(f'<g class="tick" transform="translate(0,{200 - 20 * v})"><text>{v}</text></g>' for v in (0, 5))
    rects = '<rect class="mark" x="0" y="140" width="40" height="60"/><rect class="mark" x="40" y="180" ' \
            'width="40" height="20"/>'
    r = readers.read_chart((f'<svg xmlns="http://www.w3.org/2000/svg"><g class="axis x">{xt}</g><g class="axis y">'
                            f'{yt}</g><g class="plot">{rects}</g></svg>').encode())
    assert [(tuple(round(v, 9) + 0.0 for v in m.x_extent), round(m.y, 6)) for m in r.marks] == \
        [((0.0, 1.0), 3.0), ((1.0, 2.0), 1.0)]
    days = ("2024-01-01", "2024-01-11", "2024-01-21")
    xt = "".join(f'<g class="tick" transform="translate({100 * k},0)"><text>{d}</text></g>' for k, d in enumerate(days))
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg"><g class="axis x">{xt}</g><g class="axis y">{yt}</g>'
           '<g class="plot"><circle class="mark" cx="50" cy="200"/></g></svg>').encode()
    r = readers.read_chart(svg)
    assert r.x.kind == "time"
    assert r.marks[0].x == pytest.approx(1704067200 + 5 * 86400)          # 2024-01-06 UTC


def test_unreadable_charts_raise():
    with pytest.raises(readers.ReadError):
        readers.read_chart(b'<svg xmlns="http://www.w3.org/2000/svg"><g class="plot"/></svg>')
    with pytest.raises(readers.ReadError):
        readers.read_chart(b'<!DOCTYPE svg [<!ENTITY a "b">]><svg/>')
    with pytest.raises(readers.ReadError):        # tick labels that contradict their positions
        readers.fit_scale([(0, "0"), (10, "1"), (20, "5")])


def test_diagram_nodes_and_edges():
    def diagram(edge_to):
        nodes = "".join(f'<g class="node" transform="translate({x},{y})"><circle r="10"/><text>{t}</text></g>'
                        for t, x, y in (("root", 100, 20), ("left", 50, 100), ("right", 150, 100)))
        return (f'<svg xmlns="http://www.w3.org/2000/svg">{nodes}<line class="edge" x1="100" y1="30" x2="50" y2="90"/>'
                f'<path class="edge" d="M 100 30 L {edge_to} 90"/></svg>').encode()
    r = readers.read_diagram(diagram(150))
    assert r.labels == ["root", "left", "right"]
    assert sorted(r.edges) == [("root", "left"), ("root", "right")]
    assert ("root", "right") not in readers.read_diagram(diagram(50)).edges


# ---- SVG safety and palette ----------------------------------------------------------------------------------------


@pytest.mark.parametrize("bad", [
    '<script>alert(1)</script>', '<rect onload="x()"/>', '<image href="http://example.invalid/a.png"/>',
    '<foreignObject><div/></foreignObject>', '<use href="other.svg#a"/>', '<rect style="fill:url(http://x/y)"/>',
    '<style>@import "http://x/y.css";</style>', '<a href="javascript:alert(1)"><text>x</text></a>'])
def test_unsafe_svg_is_caught(bad):
    assert readers.svg_safety(f'<svg xmlns="http://www.w3.org/2000/svg">{bad}</svg>'.encode())


def test_safe_svg_passes():
    assert readers.svg_safety(line_chart([1.0, 2.0])) == []
    assert readers.svg_safety(b'<svg xmlns="http://www.w3.org/2000/svg"><defs><linearGradient id="g"/></defs>'
                              b'<rect fill="url(#g)"/><use href="#g"/></svg>') == []


def test_palette_rule():
    assert readers.svg_palette(line_chart([1.0, 2.0])) == []
    assert readers.svg_palette(line_chart([1.0, 2.0], hue_mark=1))            # a hue nobody asked for
    warned = line_chart([1.0, 2.0]).replace(b'<circle class="mark" cx="150"',
                                            b'<circle class="mark warn" fill="#d22" cx="150"')
    assert readers.svg_palette(warned, warn_allowed=1) == []
    assert readers.svg_palette(warned, warn_allowed=0)
    for gray in ("#777", "#3c3c3c", "rgb(10, 10, 10)", "hsl(200, 0%, 40%)", "currentColor", "none", "dimgray"):
        assert readers.color_is_gray(gray), gray
    for hue in ("#d22", "#3c3c3d", "rgb(10,11,10)", "teal", "hsl(200, 50%, 40%)"):
        assert readers.color_is_gray(hue) is False, hue
    css = b'<svg xmlns="http://www.w3.org/2000/svg"><style>.a { fill: #0a0; }</style></svg>'
    assert readers.svg_palette(css)


# ---- WAV ---------------------------------------------------------------------------------------------------------------


def wav_bytes(x: np.ndarray, rate: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes((np.clip(x, -1, 1) * 32767).astype("<i2").tobytes())
    return buf.getvalue()


def test_tone_pitch_and_a_wrong_pitch():
    rate = 22050
    t = np.arange(int(0.5 * rate)) / rate
    s = readers.read_wav(wav_bytes(0.5 * np.sin(2 * np.pi * 440 * t), rate))
    assert s.rate == 22050 and s.duration_s == pytest.approx(0.5, abs=1e-4)
    f = readers.dominant_hz(s.samples, s.rate)
    assert f == pytest.approx(440, rel=0.002)
    wrong = readers.read_wav(wav_bytes(0.5 * np.sin(2 * np.pi * 452 * t), rate))
    assert abs(readers.dominant_hz(wrong.samples, wrong.rate) - 440) / 440 > 0.02


def test_sonified_series_recovered_and_onsets():
    rate, values, lo, hi = 16000, [2.0, 9.0, 5.0, 5.0, 0.0], 220.0, 880.0
    pitch = [lo + (v - 0) / 9 * (hi - lo) for v in values]
    tone = int(0.2 * rate)
    x = np.concatenate([np.zeros(800)] + [0.4 * np.sin(2 * np.pi * f * np.arange(tone) / rate) for f in pitch])
    s = readers.read_wav(wav_bytes(x, rate))
    got = [(f - lo) / (hi - lo) * 9 for f in readers.tone_pitches(s, len(values))]
    assert np.allclose(got, values, atol=0.09)
    gaps = np.concatenate([np.concatenate([0.4 * np.sin(2 * np.pi * 300 * np.arange(tone) / rate), np.zeros(2400)])
                           for _ in range(3)])
    assert readers.onsets(readers.read_wav(wav_bytes(gaps, rate))) == 3


def test_wav_damage_is_named():
    good = wav_bytes(np.zeros(100), 8000)
    with pytest.raises(readers.ReadError):
        readers.read_wav(good[:-50])                    # data chunk shorter than declared
    with pytest.raises(readers.ReadError):
        readers.read_wav(b"RIFX" + good[4:])
    floats = good.replace(struct.pack("<HH", 1, 1), struct.pack("<HH", 3, 1), 1)
    with pytest.raises(readers.ReadError):
        readers.read_wav(floats)


# ---- meshes ------------------------------------------------------------------------------------------------------------

UNIT_CUBE = [  # 12 outward triangles of the unit cube, written out by hand
    ((0, 0, 0), (0, 1, 0), (1, 1, 0)), ((0, 0, 0), (1, 1, 0), (1, 0, 0)),
    ((0, 0, 1), (1, 0, 1), (1, 1, 1)), ((0, 0, 1), (1, 1, 1), (0, 1, 1)),
    ((0, 0, 0), (1, 0, 0), (1, 0, 1)), ((0, 0, 0), (1, 0, 1), (0, 0, 1)),
    ((0, 1, 0), (0, 1, 1), (1, 1, 1)), ((0, 1, 0), (1, 1, 1), (1, 1, 0)),
    ((0, 0, 0), (0, 0, 1), (0, 1, 1)), ((0, 0, 0), (0, 1, 1), (0, 1, 0)),
    ((1, 0, 0), (1, 1, 0), (1, 1, 1)), ((1, 0, 0), (1, 1, 1), (1, 0, 1))]


def binary_stl(tris, scale=2.0, normals=None) -> bytes:
    out = b"x" * 80 + struct.pack("<I", len(tris))
    for k, (a, b, c) in enumerate(tris):
        a, b, c = (np.array(p, float) * scale for p in (a, b, c))
        n = normals[k] if normals else np.cross(b - a, c - a) / np.linalg.norm(np.cross(b - a, c - a))
        out += struct.pack("<12fH", *n, *a, *b, *c, 0)
    return out


def test_cube_volume_watertight_and_normals():
    m = readers.read_mesh(binary_stl(UNIT_CUBE))
    assert m.volume == pytest.approx(8.0) and m.area == pytest.approx(24.0)
    assert m.edge_report() == (True, True) and m.normals_agree() is True
    assert m.bounds == ([0.0, 0.0, 0.0], [2.0, 2.0, 2.0])


def test_inverted_face_and_flipped_normal_are_caught():
    flipped = list(UNIT_CUBE)
    a, b, c = flipped[3]
    flipped[3] = (a, c, b)
    m = readers.read_mesh(binary_stl(flipped))
    assert m.edge_report() == (True, False)
    assert m.volume != pytest.approx(8.0)
    normals = [np.cross(np.subtract(b, a), np.subtract(c, a)) for a, b, c in UNIT_CUBE]
    normals[5] = -normals[5]
    assert readers.read_mesh(binary_stl(UNIT_CUBE, normals=normals)).normals_agree() is False
    open_box = readers.read_mesh(binary_stl(UNIT_CUBE[:-2]))
    assert open_box.edge_report()[0] is False


def test_binary_stl_whose_header_says_solid_and_obj_with_negative_indices():
    data = b"solid trap" + binary_stl(UNIT_CUBE)[10:]
    assert readers.read_mesh(data).volume == pytest.approx(8.0)
    obj = b"v 0 0 0\nv 1 0 0\nv 0 1 0\nv 0 0 1\nf -4 -2 -3\nf -4 -3 -1\nf -4 -1 -2\nf -3 -2 -1\n"
    m = readers.read_mesh(obj)
    assert m.volume == pytest.approx(1 / 6) and m.edge_report() == (True, True)


# ---- PNG and APNG ---------------------------------------------------------------------------------------------------


def chunk(kind: bytes, body: bytes) -> bytes:
    return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))


def png_by_hand(w, h, bits, color, rows: bytes, extra=b"") -> bytes:
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, bits, color, 0, 0, 0)) + extra
            + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))


def test_png_by_hand():
    gray = readers.read_png(png_by_hand(2, 2, 8, 0, b"\x00\x0a\x14" + b"\x02\x01\x01"))   # Up filter on row 2
    assert gray.frames[0][:, :, 0].tolist() == [[10, 20], [11, 21]]
    pal = readers.read_png(png_by_hand(3, 1, 8, 3, b"\x00\x00\x01\x02",
                                       chunk(b"PLTE", bytes([9, 8, 7, 1, 2, 3, 200, 100, 50]))
                                       + chunk(b"tRNS", b"\x80")))
    assert pal.frames[0].tolist() == [[[9, 8, 7, 128], [1, 2, 3, 255], [200, 100, 50, 255]]]
    deep = readers.read_png(png_by_hand(1, 1, 16, 2, b"\x00" + struct.pack(">3H", 65535, 256, 1)))
    assert deep.frames[0].tolist() == [[[65535, 256, 1]]]
    packed = readers.read_png(png_by_hand(5, 1, 2, 0, b"\x00" + bytes([0b00011011, 0b11000000])))
    assert packed.frames[0][:, :, 0].tolist() == [[0, 1, 2, 3, 3]]
    sub = readers.read_png(png_by_hand(3, 1, 8, 0, b"\x01\x05\x05\x05"))                   # Sub filter
    assert sub.frames[0][:, :, 0].tolist() == [[5, 10, 15]]


def test_png_damage_is_named():
    good = png_by_hand(1, 1, 8, 0, b"\x00\x07")
    bad_crc = good[:-5] + bytes([good[-5] ^ 1]) + good[-4:]
    for broken in (bad_crc, good[:20], b"\x89PNX" + good[4:]):
        with pytest.raises(readers.ReadError):
            readers.read_png(broken)
    huge = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 100000, 100000, 8, 0, 0, 0, 0))
    with pytest.raises(readers.ReadError):
        readers.read_png(huge)


def apng_by_hand(frames, delays):
    w, h = len(frames[0][0]), len(frames[0])
    out = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 0, 0, 0, 0))
    out += chunk(b"acTL", struct.pack(">II", len(frames), 0))
    seq = 0
    for i, (frame, (num, den)) in enumerate(zip(frames, delays)):
        out += chunk(b"fcTL", struct.pack(">IIIIIHHBB", seq, w, h, 0, 0, num, den, 0, 0))
        seq += 1
        data = zlib.compress(b"".join(b"\x00" + bytes(row) for row in frame))
        if i == 0:
            out += chunk(b"IDAT", data)
        else:
            out += chunk(b"fdAT", struct.pack(">I", seq) + data)
            seq += 1
    return out + chunk(b"IEND", b"")


def test_apng_frames_delays_and_a_wrong_delay():
    frames = [[[0, 50], [100, 150]], [[255, 0], [0, 255]], [[7, 7], [7, 7]]]
    r = readers.read_png(apng_by_hand(frames, [(1, 10), (25, 100), (1, 2)]))
    assert r.animated and len(r.frames) == 3
    assert [f[:, :, 0].tolist() for f in r.frames] == frames
    assert r.delays_s == pytest.approx([0.1, 0.25, 0.5])
    wrong = readers.read_png(apng_by_hand(frames, [(1, 10), (30, 100), (1, 2)]))
    assert wrong.delays_s[1] != pytest.approx(0.25)
    assert readers.png_is_gray(r)


def test_png_hue_is_seen():
    rgb = png_by_hand(2, 1, 8, 2, b"\x00" + bytes([9, 9, 9, 9, 9, 10]))
    assert not readers.png_is_gray(readers.read_png(rgb))


# ---- encoders against the standard library -------------------------------------------------------------------------


def test_deflate_variants_inflate_with_zlib():
    data = bytes(range(256)) * 9 + b"repeat " * 300
    assert zlib.decompress(enc.deflate_stored(data), -15) == data
    assert zlib.decompress(enc.deflate_fixed(data), -15) == data
    assert len(enc.deflate_fixed(data)) < len(data) // 3
    for n in (1, 2, 258, 259, 517, 100000):
        assert zlib.decompress(enc.deflate_run(n, 0x41), -15) == b"A" * n
    bomb = enc.deflate_run(1 << 28)
    assert len(bomb) < (1 << 28) // 900                     # about 1000:1
    d = zlib.decompressobj(-15)
    assert d.decompress(bomb, 1 << 16) == bytes(1 << 16)


def test_containers_read_back_with_the_standard_library():
    payload = b"alpha,beta\n1,2\n" * 50
    assert zlib.decompress(enc.zlib_stream(payload)) == payload
    assert gzip.decompress(enc.gzip_member(payload, name="x.csv")) == payload
    z = zipfile.ZipFile(io.BytesIO(enc.zip_files({"a/b.csv": payload, "c.txt": b"x"})))
    assert z.testzip() is None and z.read("a/b.csv") == payload and z.namelist() == ["a/b.csv", "c.txt"]
    t = tarfile.open(fileobj=io.BytesIO(enc.tar_archive({"d/e.txt": b"hello"}, symlinks={"link": "/etc/x"})))
    assert t.extractfile("d/e.txt").read() == b"hello" and t.getmember("link").issym()
    zstandard = pytest.importorskip("zstandard")
    for size in (0, 3, 300, 70000, 300000):
        assert zstandard.ZstdDecompressor().decompress(enc.zstd_raw_frame(b"q" * size), max_output_size=1 << 20) \
            == b"q" * size
    a = np.arange(12, dtype=">f4").reshape(3, 4)
    back = np.load(io.BytesIO(enc.npy(a, fortran=True)), allow_pickle=False)
    assert back.dtype == np.dtype(">f4") and np.array_equal(back, a) and back.flags.f_contiguous


def test_gif_matches_the_canonical_one_pixel_file():
    known = bytes.fromhex("47494638396101000100800000000000ffffff")    # header, screen, 2-colour table
    out = enc.gif([np.zeros((1, 1), np.uint8)], [(0, 0, 0), (255, 255, 255)], [0])
    assert out[:10] == known[:10] and out[10] & 0x87 == known[10] & 0x87     # colour resolution bits are free
    assert out[13:19] == known[13:19]
    assert b"\x2c\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02\x44\x01\x00\x3b" in out


def test_wav_and_mesh_encoders_read_back():
    x = np.array([0.0, 0.5, -0.5, 1.0])
    for bits in (8, 16, 24):
        with wave.open(io.BytesIO(enc.wav(x, 8000, bits=bits))) as w:
            assert (w.getsampwidth(), w.getnframes(), w.getframerate()) == (bits // 8, 4, 8000)
        back = readers.read_wav(enc.wav(x, 8000, bits=bits)).samples[:, 0]
        assert np.allclose(back, enc.quantised(x, bits))
    verts = [[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]]
    tris = [[0, 2, 1], [0, 1, 3], [0, 3, 2], [1, 2, 3]]
    for data in (enc.stl_binary(np.array(verts, float), tris), enc.stl_ascii(np.array(verts, float), tris),
                 enc.obj(verts, tris), enc.ply(verts, tris)):
        m = readers.read_mesh(data)
        assert m.volume == pytest.approx(1 / 6) and m.edge_report() == (True, True)


def test_png_encoder_filters_and_interlace_decode():
    rng = np.random.default_rng(0)
    for mode, ch in (("gray", 1), ("rgb", 3), ("rgba", 4)):
        for bits in (8, 16):
            top = (1 << bits) - 1
            arr = rng.integers(0, top + 1, size=(9, 11, ch)).astype(np.uint16 if bits == 16 else np.uint8)
            arr = arr[:, :, 0] if ch == 1 else arr
            for interlace in (False, True):
                got = readers.read_png(enc.png(arr, mode=mode, bits=bits, interlace=interlace)).frames[0]
                assert np.array_equal(got.reshape(arr.shape), arr), (mode, bits, interlace)
    # the first row with filter 0 is the raw samples, byte for byte
    raw = zlib.decompress(enc.png(np.array([[7, 9]], np.uint8), mode="gray")[33 + 8:-12 - 4])
    assert raw == b"\x00\x07\x09"
