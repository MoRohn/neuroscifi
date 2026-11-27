"""Lane FX, item 1: identification that cannot be fooled into a false answer (ADR-080).

Each class of false identification the lane found, with a test that fails without its rule: a name or a declared
type alone naming a binary format; a hint naming a text format the bytes do not confirm; a name refining a ZIP whose
whole directory was seen; a PDF header buried in other bytes or followed by words; a header reader reporting
"facts" from a signature followed by other bytes; a NetPBM header followed by something else; text that only
mentions an STL or PLY header line."""
from __future__ import annotations

import io
import struct
import zipfile

import numpy as np
import pytest

from neuroscifi.artifacts import adapters, types as T
from neuroscifi.artifacts.detect import detect

BINARY_NAMES = ("photo.jpg", "scan.png", "song.mp3", "clip.mp4", "doc.pdf", "a.zip", "s.wav", "m.stl", "x.psd",
                "d.sqlite", "t.tar", "f.flac", "g.gif", "b.bmp", "v.mkv", "p.glb", "n.npy", "h.heic", "w.webp")
DECLARED = ("image/png", "image/jpeg", "audio/mpeg", "video/mp4", "application/pdf", "application/zip",
            "model/stl", "image/tiff", "application/x-7z-compressed")


def _noise(seed: int, n: int = 700) -> bytes:
    return np.random.default_rng(40000 + seed).integers(0, 256, n).astype(np.uint8).tobytes()


def _answered(data: bytes, **kw):
    """(format named, content answered) as the benchmark's driver would see them."""
    try:
        out = adapters.decode(data, **kw)
    except T.ArtifactError:
        return detect(data, size=len(data), **kw).format, False
    return out.detected.format, out.representation.kind != "opaque"


@pytest.mark.parametrize("name", BINARY_NAMES)
def test_a_name_alone_never_identifies_a_binary_format(name):
    for i, data in enumerate((_noise(len(name)), bytes(512), b"QXR1" + struct.pack("<IfI", 1, 0.5, 2) * 40)):
        fmt, answered = _answered(data, name=name)
        assert fmt == "unknown" and not answered, (name, i, fmt)
        assert "never identifies a file" in " ".join(detect(data, name=name, size=len(data)).reasons)


@pytest.mark.parametrize("declared", DECLARED)
def test_a_declared_type_alone_never_identifies_a_binary_format(declared):
    fmt, answered = _answered(_noise(len(declared)), declared=declared)
    assert fmt == "unknown" and not answered


def test_a_text_hint_must_be_confirmed_by_its_reader():
    latin = '<?xml version="1.0" encoding="ISO-8859-1"?>\n<menu><item>café crème</item></menu>\n'.encode("latin-1")
    out = adapters.decode(latin, name="menu.xml")                 # honest: confirmed by the XML reader
    assert out.detected.format == "xml" and out.representation.kind == "tree"
    words = "Bientôt l'été, déjà fini; ceci n'est pas du JSON.\n".encode("latin-1")
    out = adapters.decode(words, name="data.json")                # the JSON reader cannot confirm the name
    assert out.detected.format == "unknown" and out.representation.kind == "opaque"
    assert any("not confirmed" in r for r in out.detected.reasons)


def test_a_pickle_name_still_refuses():
    with pytest.raises(T.Refused):
        adapters.decode(b"\x00\x01\x02", name="model.pkl")


def _zip(files) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, body in files.items():
            z.writestr(name, body)
    return buf.getvalue()


def test_a_name_does_not_refine_an_archive_whose_members_were_all_seen():
    data = _zip({"a.txt": b"alpha\n", "b.txt": b"beta\n"})
    assert detect(data, name="report.docx", size=len(data)).format == "zip"
    out = adapters.decode(data, name="report.docx")
    assert out.detected.format == "zip" and len(out.representation.members) == 2


def test_an_archive_longer_than_the_prefix_named_as_office_is_read_as_the_archive():
    data = _zip({"a.bin": _noise(1, 90_000), "b.txt": b"beta\n"})  # members beyond the 64 KiB prefix
    assert detect(data[:65536], name="report.docx", size=len(data)).format == "docx"
    out = adapters.decode(data, name="report.docx")
    assert out.detected.format == "zip" and any("not readable as docx" in n for n in out.notes)


def test_a_pdf_header_needs_what_follows_every_pdf_header():
    buried = bytearray(_noise(2, 3000))
    buried[100:109] = b"%PDF-1.4\n"
    assert detect(bytes(buried), size=len(buried)).format == "unknown"
    words = b"%PDF-1.7 was mentioned in the call, nothing else.\n" * 3
    assert detect(words, size=len(words)).format != "pdf"
    assert detect(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n", size=1 << 20).format == "pdf"    # a head of a real one
    assert detect(b"%PDF-1.4\n", size=1 << 20).format == "pdf"                        # cut right after the header
    junk_then_pdf = b"x" * 200 + b"%PDF-1.4\n1 0 obj\n<< >>\nendobj\n"
    assert detect(junk_then_pdf, size=len(junk_then_pdf)).format == "pdf"


def test_text_that_mentions_a_mesh_header_is_not_a_mesh():
    stl_words = b"solid ground, they said.\nfacet normal is a phrase from a file format.\n" * 3
    assert detect(stl_words, size=len(stl_words)).format != "stl"
    ply_words = b"ply\nformat ascii 1.0 is a header line someone typed\n"
    assert detect(ply_words, size=len(ply_words)).format != "ply"


def _psd(version=1, channels=3, height=4, width=5, depth=8, mode=3, reserved=b"\x00" * 6) -> bytes:
    return (b"8BPS" + struct.pack(">H", version) + reserved + struct.pack(">HIIHH", channels, height, width, depth, mode)
            + struct.pack(">I", 0) * 3 + b"\x00" * 8)


def test_a_header_reader_reports_facts_only_from_a_valid_header():
    with pytest.raises(T.HeaderOnly) as e:
        adapters.decode(_psd())
    assert (e.value.facts["width"], e.value.facts["height"]) == (5, 4)
    for bad in (_psd(width=0), _psd(channels=99), _psd(depth=7), _psd(mode=5), _psd(reserved=b"\x01" * 6),
                b"8BPS\x00\x01" + _noise(3, 60)):
        with pytest.raises(T.Malformed):
            adapters.decode(bad)


def test_a_netpbm_header_followed_by_something_else_is_not_answered():
    image = b"P5\n2 2\n255\n" + bytes([1, 2, 3, 4])
    assert adapters.decode(image + b"\n").representation.kind == "raster"
    assert adapters.decode(image + image).representation.kind == "raster"      # a second image: allowed
    assert adapters.decode(image + b"# made by hand\n").representation.kind == "raster"   # a comment line: allowed
    with pytest.raises(T.Malformed):
        adapters.decode(image + _noise(4, 40))
