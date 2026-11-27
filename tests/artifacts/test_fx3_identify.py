"""Lane FX3 (ADR-081), identification classes U-A to U-D found on the spent withheld cases. Every file here is built
by this test from its own bytes (none is a spent case): text that begins with a binary format's signature, Python
source under a plain-text name, Markdown whose only marker is its headings, a ZIP after leading bytes no format
claims. Each test fails before the lane's rule."""
from __future__ import annotations

import io
import struct
import zipfile

import numpy as np
import pytest

from neuroscifi.artifacts import adapters, types as T
from neuroscifi.artifacts.detect import detect


def _decoded(data: bytes, **kw):
    out = adapters.decode(data, **kw)
    return out.detected.format, out.representation


# ---- U-A: a binary signature at the start of text ---------------------------------------------------------------

PROSE = ("The meeting moved to Thursday because the hall was booked.\n"
         "Bring the printed agenda and the two budget sheets.\n")


@pytest.mark.parametrize("opening", [
    "RIFF....WAVEfmt is how a wave file begins; this note only says so.\n",
    "RIFF....AVI is a container named in the old manual.\n",
    "FORM....AIFF was the other format we discussed.\n",
    "fLaC is the four-letter marker of a lossless stream.\n",
    "GIF89a was the version that added transparency.\n",
    "GIF87a came first, in nineteen eighty-seven.\n",
])
def test_text_that_begins_with_a_binary_signature_is_text(opening):
    data = (opening + PROSE).encode()
    fmt, rep = _decoded(data, name="README")
    assert fmt in ("text", "markdown"), (opening, fmt)
    assert rep.kind == "document" and len(rep.text()) > 40
    assert any("part of the words" in r for r in detect(data, size=len(data)).reasons)


def test_a_table_whose_first_header_cell_is_a_signature_is_a_table():
    rows = "".join(f"{i},{i * 37 % 500 + 10},{i * 53 % 400 + 20}\n" for i in range(1, 9))
    data = ("GIF89a,width,height\n" + rows).encode()
    fmt, rep = _decoded(data, name="sizes")
    assert fmt == "csv" and rep.kind == "table" and rep.n_rows == 8
    assert [c.name for c in rep.columns] == ["GIF89a", "width", "height"]


def test_real_binary_files_keep_their_signature():
    # a real WAV and a real GIF (hand-assembled): binary fields, so the signature stands
    pcm = struct.pack("<8h", *range(0, 800, 100))
    fmt_chunk = struct.pack("<HHIIHH", 1, 1, 8000, 16000, 2, 16)
    body = b"WAVE" + b"fmt " + struct.pack("<I", 16) + fmt_chunk + b"data" + struct.pack("<I", len(pcm)) + pcm
    wav = b"RIFF" + struct.pack("<I", len(body)) + body
    assert detect(wav, size=len(wav)).format == "wav"
    gif = (b"GIF89a" + struct.pack("<HHBBB", 2, 2, 0x80, 0, 0) + b"\x00\x00\x00\xff\xff\xff"
           + b"\x2c" + struct.pack("<HHHHB", 0, 0, 2, 2, 0) + b"\x02\x02\x44\x01\x00\x3b")
    assert detect(gif, size=len(gif)).format == "gif"
    # a text format whose signature is text by design is not doubted
    pdf_head = b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\n"
    assert detect(pdf_head, size=len(pdf_head)).format == "pdf"


def test_a_short_text_prefix_of_a_longer_file_does_not_doubt_the_signature():
    head = b"RIFF....WAVE plain words" * 4          # 96 bytes seen of a file said to be much longer
    assert detect(head, size=1 << 20).format == "wav"


# ---- U-B: Python source under a plain-text name -------------------------------------------------------------------

SOURCE = ('"""Helpers for the rota."""\nimport datetime\n\n\ndef week(day):\n    return day.isocalendar()[1]\n\n\n'
          'def label(day):\n    return f"week {week(day)}"\n')


@pytest.mark.parametrize("name,declared", [("rota.txt", "text/plain"), ("rota", None), ("notes.text", None)])
def test_python_source_is_python_whatever_its_name(name, declared):
    data = SOURCE.encode()
    out = adapters.decode(data, name=name, declared=declared)
    assert out.detected.format == "python" and out.detected.family == "code"
    labels = sorted(n.label.rsplit(".", 1)[-1].split("(")[0] for n in out.representation.nodes if n.kind == "function")
    assert labels == ["label", "week"]


@pytest.mark.parametrize("text", [
    "from the station we walked north,\nimport duties were paid at the border.\n",   # starts like Python, is not
    "a,b\n1,2\n3,4\n",                                                              # parses as Python, a table
    "x = 1\ny = 2\n",                                                               # Python with no def/class/import
])
def test_text_that_is_not_a_python_module_is_not_named_python(text):
    assert detect(text.encode(), name="notes.txt", size=len(text)).format != "python"


# ---- U-C: Markdown whose only marker is its headings ---------------------------------------------------------------

DOC = "## Arrival\n\nThe train was late by ten minutes.\n\n## Departure\n\nWe left at noon.\n\n## Costs\n\nNone.\n"


def test_headings_alone_make_markdown_and_are_read():
    data = ("Draft, kept as plain text\n\n" + DOC).encode()
    for kw in ({}, {"name": "trip.pdf", "declared": "application/pdf"}, {"name": "trip.txt"}):
        out = adapters.decode(data, **kw)
        assert out.detected.format == "markdown", kw
        assert [b.text for b in out.representation.blocks if b.kind == "heading"] == ["Arrival", "Departure", "Costs"]


@pytest.mark.parametrize("name", ["settings.yaml", "setup.cfg", "pyproject.toml", "run.sh"])
def test_a_hint_naming_a_format_with_hash_comments_keeps_headings_alone_from_making_markdown(name):
    data = b"# Paths\n\nroot = here\n\n# Limits\n\nsize = 3\n"
    assert detect(data, name=name, size=len(data)).format != "markdown"


def test_hash_lines_that_are_not_blocks_are_not_headings():
    data = b"# one\n# two\nname: value\n"
    assert detect(data, size=len(data)).format == "text"


# ---- U-D: a ZIP after leading bytes no format claims ---------------------------------------------------------------

def _zip(files: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, body in files.items():
            z.writestr(zipfile.ZipInfo(name, (2020, 1, 1, 0, 0, 0)), body)
    return buf.getvalue()


def _after(prefix: bytes, files: dict) -> bytes:
    """`prefix` + a ZIP whose offsets count the prefix (zipfile writes it so when appending to a stub)."""
    buf = io.BytesIO()
    buf.write(prefix)
    with zipfile.ZipFile(buf, "a", zipfile.ZIP_DEFLATED) as z:
        for name, body in files.items():
            z.writestr(zipfile.ZipInfo(name, (2020, 1, 1, 0, 0, 0)), body)
    return buf.getvalue()


def _stub(seed: int, n: int) -> bytes:
    return b"MZ" + np.random.default_rng(seed).integers(0, 256, n).astype(np.uint8).tobytes()


@pytest.mark.parametrize("prefix", [_stub(7, 300), _stub(8, 2000), b"\x00\x01\x02\x03" * 40])
def test_a_zip_after_unclaimed_leading_bytes_is_read_as_a_zip(prefix):
    files = {"kit/list.csv": b"item,n\nbolt,4\nnut,9\n", "kit/readme.txt": b"spares\n"}
    data = _after(prefix, files)
    fmt, rep = _decoded(data, name="setup.dat")
    assert fmt == "zip" and rep.kind == "bundle"
    assert sorted(m.path for m in rep.members) == sorted(files)
    assert any("leading bytes" in r for r in adapters.decode(data).detected.reasons)


def test_a_zip_after_a_recognised_format_is_still_a_polyglot():
    png = (b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + struct.pack(">IIBBBBB", 1, 1, 8, 0, 0, 0, 0)
           + b"\x00\x00\x00\x00")
    data = _after(png, {"a.txt": b"x\n"})
    with pytest.raises(T.Refused):
        adapters.decode(data, name="pic.png")


def test_unknown_bytes_with_a_buried_zip_marker_stay_unknown():
    noise = bytearray(np.random.default_rng(11).integers(0, 256, 3000).astype(np.uint8).tobytes())
    noise[700:704] = b"PK\x03\x04"
    noise[-22:-18] = b"PK\x05\x06"               # an end-record marker with nothing behind it
    fmt, rep = _decoded(bytes(noise), name="memory.raw")
    assert fmt == "unknown" and rep.kind == "opaque"
    plain = _zip({"a.txt": b"x\n"})               # a ZIP at offset 0 is identified by its signature, as before
    assert detect(plain, size=len(plain)).format == "zip"
