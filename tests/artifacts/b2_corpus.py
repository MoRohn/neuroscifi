"""One small valid sample per lane-B2 format, built by the independent encoders of the B2 test files (used by the
detection, fuzz and evidence checks). Returns {name: (expected format, bytes)}."""
from __future__ import annotations

import io
import json
import struct
import zipfile

import numpy as np


def samples() -> dict[str, tuple[str, bytes]]:
    from b2_fixtures import fmt_chunk, png, riff
    from test_b2_arrays import _saved
    from test_b2_headers import (_dicom, _fits, _flac, _jpeg, _mp4, _mtrk, _ogg_page, _tiff_image, _box)
    from test_b2_meshes import CUBE_Q, CUBE_V, _gltf, _ply_header, _stl_binary, _tris_of_quads
    from test_b2_rasters import _bmp, _gif, _pad, _qoi_encode
    from test_b2_sound import _ext80, _iff, _pcm16
    from test_b2_svg_geo import GEOJSON, GPX, KML, _svg

    rng = np.random.default_rng(0)
    out: dict[str, tuple[str, bytes]] = {}
    img = [[tuple(int(v) for v in rng.integers(0, 256, 3)) for _ in range(8)] for _ in range(8)]
    out["png"] = ("png", png(img, filters=(0, 1, 2, 3, 4), interlace=1))
    out["ppm"] = ("ppm", b"P6 8 8 255\n" + np.array(img, np.uint8).tobytes())
    out["pgm"] = ("pgm", b"P2 2 2 255\n0 1 2 3\n")
    out["pbm"] = ("pbm", b"P1\n2 2\n0 1 1 0\n")
    out["pam"] = ("pam", b"P7\nWIDTH 1\nHEIGHT 1\nDEPTH 3\nMAXVAL 255\nENDHDR\n\x01\x02\x03")
    out["farbfeld"] = ("farbfeld", b"farbfeld" + struct.pack(">II", 1, 1) + b"\x00" * 8)
    out["qoi"] = ("qoi", _qoi_encode(np.array(img, np.uint8), 3))
    out["bmp"] = ("bmp", _bmp(2, 1, 24, _pad(b"\x01\x02\x03\x04\x05\x06")))
    out["gif"] = ("gif", _gif(4, 4, [(0, 0, 4, 4, [1, 0] * 8, 5, 0, None, False)] * 2, [(0, 0, 0), (255, 255, 255)]))
    ico_png = png([[(1, 2, 3)]])
    out["ico"] = ("ico", struct.pack("<HHH", 0, 1, 1) + struct.pack("<BBBBHHII", 1, 1, 0, 0, 1, 32, len(ico_png), 22)
                  + ico_png)
    out["tiff"] = ("tiff", _tiff_image(2, 2, bytes(range(12))))
    out["jpeg"] = ("jpeg", _jpeg())
    vp8 = b"\x00\x00\x00\x9d\x01\x2a" + struct.pack("<HH", 32, 16) + b"\x00" * 10
    out["webp"] = ("webp", b"RIFF" + struct.pack("<I", 4 + 8 + len(vp8)) + b"WEBP" + b"VP8 " +
                   struct.pack("<I", len(vp8)) + vp8)
    ispe = _box(b"ispe", b"\x00" * 4 + struct.pack(">II", 64, 48))
    out["heic"] = ("heic", _box(b"ftyp", b"heic\x00\x00\x00\x00mif1heic") +
                   _box(b"meta", b"\x00" * 4 + _box(b"iprp", _box(b"ipco", _box(b"hvcC", b"\x01") + ispe))))
    out["psd"] = ("psd", b"8BPS" + struct.pack(">H", 1) + b"\x00" * 6 + struct.pack(">HIIHH", 3, 4, 4, 8, 3) +
                  b"\x00" * 16)
    out["dicom"] = ("dicom", _dicom(2, 2, np.arange(4, dtype="<u2").tobytes()))
    out["fits"] = ("fits", _fits(8, 2, 2, bytes([1, 2, 3, 4])))
    out["svg"] = ("svg", _svg('<rect width="1" height="2"/><text x="1" y="1">t</text>'))
    out["wav"] = ("wav", riff(b"WAVE", [(b"fmt ", fmt_chunk(1, 2, 8000, 16)), (b"data", _pcm16([1, 2, 3, 4]))]))
    comm = struct.pack(">hIh", 1, 2, 16) + _ext80(8000.0)
    out["aiff"] = ("aiff", _iff(b"AIFF", [(b"COMM", comm), (b"SSND", struct.pack(">II", 0, 0) + b"\x00\x01\x00\x02")]))
    out["au"] = ("au", b".snd" + struct.pack(">IIIII", 24, 4, 3, 8000, 1) + b"\x00\x01\x00\x02")
    tr = _mtrk([(0, b"\x90\x3c\x40"), (96, b"\x80\x3c\x00")])
    out["midi"] = ("midi", b"MThd" + struct.pack(">IHHH", 6, 0, 1, 96) + tr)
    frame = bytearray(417)
    frame[:4] = b"\xff\xfb\x90\x64"
    out["mp3"] = ("mp3", bytes(frame) * 3)
    out["flac"] = ("flac", _flac())
    head = b"OpusHead\x01\x01" + struct.pack("<HI", 0, 48000) + b"\x00\x00\x00"
    out["ogg"] = ("opus", _ogg_page(1, 0, 0, [head], 2) + _ogg_page(1, 1, 4800, [b"\x00" * 9], 4))
    adts = bytes((0xFF, 0xF1, 0x50, 0x80, 0x02, 0x1F, 0xFC)) + b"\x00" * 9
    out["aac"] = ("aac", adts * 4)
    m4a = _mp4(handler=b"soun", codec=b"mp4a").replace(b"isom\x00\x00\x02\x00isomavc1", b"M4A \x00\x00\x02\x00isomM4A ")
    out["m4a"] = ("m4a", m4a)
    out["mp4"] = ("mp4", _mp4())
    from test_b2_headers import _ebml
    seg = _ebml(0x18538067, _ebml(0x1654AE6B, _ebml(0xAE, _ebml(0xD7, b"\x01") + _ebml(0x83, b"\x01") +
                                                    _ebml(0x86, b"V_VP8"))))
    out["webm"] = ("webm", _ebml(0x1A45DFA3, _ebml(0x4282, b"webm")) + seg)
    from test_b2_headers import _riff_chunk, _riff_list
    avih = _riff_chunk(b"avih", struct.pack("<10I", 40000, 0, 0, 0, 10, 0, 1, 0, 8, 8) + b"\x00" * 16)
    avi = b"RIFF" + struct.pack("<I", 0) + b"AVI " + _riff_list(b"hdrl", avih)
    out["avi"] = ("avi", avi[:4] + struct.pack("<I", len(avi) - 8) + avi[8:])
    obj = "\n".join([f"v {x} {y} {z}" for x, y, z in CUBE_V] + ["f " + " ".join(str(i + 1) for i in q) for q in CUBE_Q])
    out["obj"] = ("obj", obj.encode())
    out["stl"] = ("stl", _stl_binary([[CUBE_V[i] for i in t] for t in _tris_of_quads(CUBE_Q)]))
    body = b"".join(struct.pack("<3f", *v) for v in CUBE_V) + b"".join(struct.pack("<B4i", 4, *q) for q in CUBE_Q)
    out["ply"] = ("ply", _ply_header("binary_little_endian", 8, 6) + body)
    out["off"] = ("off", ("OFF\n8 6 0\n" + "".join(f"{x} {y} {z}\n" for x, y, z in CUBE_V) +
                          "".join(f"4 {' '.join(map(str, q))}\n" for q in CUBE_Q)).encode())
    out["gltf"] = ("gltf", _gltf())
    out["glb"] = ("glb", _gltf(binary=True))
    out["geojson"] = ("geojson", json.dumps(GEOJSON).encode())
    out["gpx"] = ("gpx", GPX)
    out["kml"] = ("kml", KML)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("doc.kml", KML)
    out["kmz"] = ("kmz", buf.getvalue())
    out["wkt"] = ("wkt", b"LINESTRING (0 0, 1 1, 2 0)")
    out["npy"] = ("npy", _saved(np.arange(6.0).reshape(2, 3)))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("a.npy", _saved(np.arange(3)))
    out["npz"] = ("npz", buf.getvalue())
    return out


__all__ = ["samples"]
