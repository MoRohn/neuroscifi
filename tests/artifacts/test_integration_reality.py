"""Reality check with files this host's own tools make (integration lane, item 6): LibreOffice, ImageMagick and
ffmpeg turn content this test writes (a short report, a table, two slides, a gray gradient, a two-tone sine) into
DOCX, ODT, PDF, XLSX, ODS, PPTX, ODP, PNG (8/16-bit, interlaced, palette), GIF, BMP, TIFF and WAV (16/24-bit,
stereo); the adapters must recover the known content from these real producers' files. The tools are used ONLY to
make test inputs here (trusted: our own content, fixed argument lists, no shell, offline, a private profile folder);
product code never calls them. Each test skips with a plain reason where its tool is absent, as in CI."""
import os
import shutil
import subprocess

import numpy as np
import pytest

from neuroscifi.artifacts import adapters
from neuroscifi.artifacts import types as T

SOFFICE, MAGICK, FFMPEG = shutil.which("soffice"), shutil.which("magick"), shutil.which("ffmpeg")
needs_soffice = pytest.mark.skipif(SOFFICE is None, reason="LibreOffice (soffice) is not installed here")
needs_magick = pytest.mark.skipif(MAGICK is None, reason="ImageMagick (magick) is not installed here")
needs_ffmpeg = pytest.mark.skipif(FFMPEG is None, reason="ffmpeg is not installed here")

REPORT = """<!DOCTYPE html><html><head><meta charset="utf-8"><title>Harbour survey</title></head><body>
<h1>Harbour survey</h1>
<p>The tide gauge read three values on Monday.</p>
<h2>Readings</h2>
<table><tr><th>site</th><th>depth_m</th></tr><tr><td>north</td><td>4.5</td></tr><tr><td>south</td><td>7.25</td></tr>
</table>
<p>Kelp beds grew near the south pier.</p>
</body></html>"""
SENTENCES = ("The tide gauge read three values on Monday.", "Kelp beds grew near the south pier.")
TABLE = "site,depth_m\nnorth,4.5\nsouth,7.25\neast,3\n"
SLIDES = """<?xml version="1.0" encoding="UTF-8"?>
<office:document xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"
 xmlns:draw="urn:oasis:names:tc:opendocument:xmlns:drawing:1.0" xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0"
 xmlns:presentation="urn:oasis:names:tc:opendocument:xmlns:presentation:1.0"
 xmlns:svg="urn:oasis:names:tc:opendocument:xmlns:svg-compatible:1.0" office:version="1.3"
 office:mimetype="application/vnd.oasis.opendocument.presentation"><office:body><office:presentation>
 <draw:page draw:name="p1"><draw:frame svg:x="2cm" svg:y="1cm" svg:width="20cm" svg:height="3cm"><draw:text-box>
  <text:p>Harbour survey</text:p></draw:text-box></draw:frame><draw:frame svg:x="2cm" svg:y="5cm" svg:width="20cm"
  svg:height="9cm"><draw:text-box><text:p>The tide gauge read three values on Monday.</text:p></draw:text-box>
  </draw:frame></draw:page>
 <draw:page draw:name="p2"><draw:frame svg:x="2cm" svg:y="1cm" svg:width="20cm" svg:height="3cm"><draw:text-box>
  <text:p>Kelp beds grew near the south pier.</text:p></draw:text-box></draw:frame></draw:page>
</office:presentation></office:body></office:document>"""


def _run(args, cwd):
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(cwd), "LC_ALL": "C.UTF-8"}
    subprocess.run(args, cwd=cwd, env=env, check=True, capture_output=True, timeout=180)


@pytest.fixture(scope="module")
def office(tmp_path_factory):
    """{format: bytes} of the report, the table and the slides, converted by LibreOffice."""
    if SOFFICE is None:
        pytest.skip("LibreOffice (soffice) is not installed here")
    root = tmp_path_factory.mktemp("office")
    (root / "report.html").write_text(REPORT, encoding="utf-8")
    (root / "table.csv").write_text(TABLE, encoding="utf-8")
    (root / "slides.fodp").write_text(SLIDES, encoding="utf-8")
    profile = (root / "profile").as_uri()
    out = root / "out"
    for source, target in (("report.html", 'docx:MS Word 2007 XML'), ("report.html", "odt"),
                           ("report.html", "pdf:writer_web_pdf_Export"), ("table.csv", "xlsx"), ("table.csv", "ods"),
                           ("slides.fodp", "pptx"), ("slides.fodp", "odp")):
        _run([SOFFICE, f"-env:UserInstallation={profile}", "--headless", "--convert-to", target, "--outdir",
              str(out), source], root)
    return {p.suffix[1:]: p.read_bytes() for p in out.iterdir()}


def _doc(data, name):
    d = adapters.decode(data, name=name)
    assert d.representation.kind == "document", (name, d.notes)
    return d.representation


@needs_soffice
@pytest.mark.parametrize("fmt", ["docx", "odt"])
def test_word_processor_files_keep_headings_paragraphs_and_the_table(office, fmt):
    doc = _doc(office[fmt], f"report.{fmt}")
    assert [b.text for b in doc.blocks if b.kind == "heading"] == ["Harbour survey", "Readings"]
    assert [b.text for b in doc.blocks if b.kind == "paragraph"] == list(SENTENCES)
    (table,) = [b.ref for b in doc.blocks if b.kind == "table"]
    cols = {c.name: c for c in table.columns}
    assert list(cols["site"].values) == ["north", "south"] and list(cols["depth_m"].values) == [4.5, 7.25]


@needs_soffice
def test_a_pdf_gives_its_text_layer(office):
    from importlib.util import find_spec
    if find_spec("pypdf") is None:
        with pytest.raises(T.Unsupported):
            adapters.decode(office["pdf"], name="report.pdf")
        return
    doc = _doc(office["pdf"], "report.pdf")
    text = " ".join(doc.text().split())
    assert all(s in text for s in SENTENCES) and "north 4.5" in text and doc.title == "Harbour survey"


@needs_soffice
@pytest.mark.parametrize("fmt", ["xlsx", "ods"])
def test_spreadsheets_keep_every_cell(office, fmt):
    rep = adapters.decode(office[fmt], name=f"table.{fmt}").representation
    (sheet,) = [m.inner for m in rep.members if m.inner is not None]
    cols = {c.name: c for c in sheet.columns}
    assert sheet.n_rows == 3 and list(cols["site"].values) == ["north", "south", "east"]
    assert cols["depth_m"].kind in ("number", "integer") and list(cols["depth_m"].values) == [4.5, 7.25, 3.0]


@needs_soffice
@pytest.mark.parametrize("fmt", ["pptx", "odp"])
def test_slides_keep_their_texts_in_order(office, fmt):
    doc = _doc(office[fmt], f"slides.{fmt}")
    assert [b.text for b in doc.blocks if b.kind == "heading"] == ["Slide 1", "Slide 2"]     # no title placeholder
    assert [b.text for b in doc.blocks if b.kind == "paragraph"] == ["Harbour survey", SENTENCES[0], SENTENCES[1]]


# ---- ImageMagick: a known gray gradient -------------------------------------------------------------------------

GRADIENT = ((np.arange(48)[None, :] * 5 + np.arange(32)[:, None] * 3) % 256).astype(np.uint8)    # 32 rows x 48


@pytest.fixture(scope="module")
def pictures(tmp_path_factory):
    if MAGICK is None:
        pytest.skip("ImageMagick (magick) is not installed here")
    root = tmp_path_factory.mktemp("pictures")
    (root / "in.pgm").write_bytes(b"P5 48 32 255\n" + GRADIENT.tobytes())
    outs = {"png8": ["-depth", "8", "png8.png"],
            "png16": ["-define", "png:bit-depth=16", "-define", "png:color-type=0", "png16.png"],
            "interlaced": ["-interlace", "PNG", "interlaced.png"], "palette": ["PNG8:palette.png"],
            "gif": ["gradient.gif"], "bmp": ["gradient.bmp"], "tiff": ["-compress", "None", "plain.tif"],
            "tiff_deflate": ["-compress", "Zip", "deflate.tif"], "tiff_lzw": ["-compress", "LZW", "lzw.tif"]}
    for args in outs.values():
        _run([MAGICK, "in.pgm", *args], root)
    files = {k: (root / v[-1].split(":")[-1]).read_bytes() for k, v in outs.items()}
    # a palette PNG and a GIF hold at most 256 colours and ImageMagick quantizes the gradient to fit: what the file
    # holds is then what ImageMagick itself reads back from it (its own reading, not ours)
    for key in ("palette", "gif"):
        name = outs[key][-1].split(":")[-1]
        _run([MAGICK, name, "-depth", "8", f"gray:{key}.raw"], root)
        files[key + "_readback"] = np.frombuffer((root / f"{key}.raw").read_bytes(), np.uint8).reshape(GRADIENT.shape)
    return files


def _gray(rep):
    px = np.asarray(rep.pixels)
    if px.ndim == 3:
        assert (px[:, :, 0] == px[:, :, 1]).all() and (px[:, :, 1] == px[:, :, 2]).all()
        px = px[:, :, 0]
    return px


@needs_magick
@pytest.mark.parametrize("case", ["png8", "interlaced", "bmp", "tiff", "tiff_deflate"])
def test_every_raster_recovers_the_gradient_exactly(pictures, case):
    rep = adapters.decode(pictures[case]).representation
    assert rep.kind == "raster", case
    assert (_gray(rep) == GRADIENT).all(), case


@needs_magick
@pytest.mark.parametrize("case", ["palette", "gif"])
def test_a_quantized_raster_recovers_what_its_producer_stored(pictures, case):
    rep = adapters.decode(pictures[case]).representation
    assert rep.kind == "raster" and (_gray(rep) == pictures[case + "_readback"]).all(), case
    assert np.abs(pictures[case + "_readback"].astype(int) - GRADIENT).max() <= 8          # near the source


@needs_magick
def test_a_16_bit_png_recovers_the_gradient_scaled(pictures):
    rep = adapters.decode(pictures["png16"]).representation
    assert rep.bits == 16 and (_gray(rep) == GRADIENT.astype(np.uint16) * 257).all()


@needs_magick
def test_an_lzw_tiff_is_identified_and_its_header_read_never_guessed(pictures):
    with pytest.raises(T.HeaderOnly) as e:
        adapters.decode(pictures["tiff_lzw"])
    assert e.value.facts["width"] == 48 and e.value.facts["height"] == 32 and "LZW" in e.value.needs[0]


# ---- ffmpeg: a known two-tone sine -------------------------------------------------------------------------------

RATE = 44100
_t = np.arange(RATE // 2) / RATE
SINE = np.stack([0.5 * np.sin(2 * np.pi * 440 * _t), 0.25 * np.sin(2 * np.pi * 660 * _t)], axis=1).astype("<f4")


@needs_ffmpeg
@pytest.mark.parametrize("codec,step", [("pcm_s16le", 1 / 32768), ("pcm_s24le", 1 / 8388608)])
def test_ffmpeg_wav_recovers_the_sine(tmp_path, codec, step):
    (tmp_path / "in.f32").write_bytes(SINE.tobytes())
    _run([FFMPEG, "-nostdin", "-loglevel", "error", "-f", "f32le", "-ar", str(RATE), "-ac", "2", "-i", "in.f32",
          "-c:a", codec, "out.wav"], tmp_path)
    rep = adapters.decode((tmp_path / "out.wav").read_bytes()).representation
    assert rep.kind == "signal" and rep.rate_hz == RATE and rep.samples.shape == SINE.shape
    assert np.abs(rep.samples.astype(np.float64) - SINE).max() <= 1.5 * step
