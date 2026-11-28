"""Lane B1 PDF: a PDF assembled byte by byte in the test (objects, xref, trailer), read through pypdf; the
header-facts answer without pypdf; the deadline guard; hostile variants."""
import importlib.util

import pytest

from neuroscifi.artifacts import types as T
from neuroscifi.artifacts.adapters import pdf as pdfmod

import _b1_support

b1reg = _b1_support.b1reg      # the fixture


def build_pdf(pages, outline_title=None, title=None):
    """pages: list of text strings, one line each. Returns the bytes of a valid PDF 1.4 with an xref table."""
    objs = {}
    n = len(pages)
    page_ids = [10 + 2 * i for i in range(n)]
    objs[1] = b"<< /Type /Catalog /Pages 2 0 R" + (b" /Outlines 4 0 R" if outline_title else b"") + b" >>"
    objs[2] = (b"<< /Type /Pages /Kids [" + b" ".join(b"%d 0 R" % p for p in page_ids) + b"] /Count %d >>" % n)
    objs[3] = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"
    if outline_title:
        objs[4] = b"<< /Type /Outlines /First 5 0 R /Last 5 0 R /Count 1 >>"
        objs[5] = (b"<< /Title (" + outline_title.encode() + b") /Parent 4 0 R /Dest [%d 0 R /Fit] >>" % page_ids[-1])
    if title:
        objs[6] = b"<< /Title (" + title.encode() + b") >>"
    for pid, text in zip(page_ids, pages):
        stream = b"BT /F1 12 Tf 72 720 Td (" + text.encode() + b") Tj ET"
        objs[pid] = (b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 3 0 R >> >>"
                     b" /Contents %d 0 R >>" % (pid + 1))
        objs[pid + 1] = b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream"
    out = bytearray(b"%PDF-1.4\n")
    offsets = {}
    for num in sorted(objs):
        offsets[num] = len(out)
        out += b"%d 0 obj\n" % num + objs[num] + b"\nendobj\n"
    size = max(objs) + 1
    xref = len(out)
    out += b"xref\n0 %d\n" % size + b"0000000000 65535 f \n"
    for num in range(1, size):
        out += (b"%010d 00000 n \n" % offsets[num]) if num in offsets else b"0000000000 65535 f \n"
    out += b"trailer\n<< /Size %d /Root 1 0 R" % size + (b" /Info 6 0 R" if title else b"") + b" >>\n"
    out += b"startxref\n%d\n%%%%EOF\n" % xref
    return bytes(out)


needs_pypdf = pytest.mark.skipif(importlib.util.find_spec("pypdf") is None, reason="pypdf not installed")


@needs_pypdf
def test_pdf_text_pages_outline_title(b1reg):
    data = build_pdf(["Hello first page", "Second page words"], outline_title="Part Two", title="Lab report")
    out = b1reg.decode(data, name="r.pdf")
    assert out.detected.format == "pdf"
    doc = out.representation
    assert doc.title == "Lab report"
    got = [(b.kind, b.text) for b in doc.blocks]
    assert got == [("paragraph", "Hello first page"), ("page_break", ""), ("heading", "Part Two"),
                   ("paragraph", "Second page words")]
    assert any("no layout, no columns, no tables" in n for n in out.notes)
    assert any("PDF version 1.4; 2 page(s) declared" in n for n in out.notes)


@needs_pypdf
def test_pdf_page_bound_marks_partial(b1reg):
    out = b1reg.decode(build_pdf([f"page {i}" for i in range(5)]), name="p.pdf", limits=T.Limits(max_members=2))
    assert out.partial and [b.text for b in out.representation.blocks if b.kind == "paragraph"] == ["page 0", "page 1"]


@needs_pypdf
def test_pdf_deadline_stops_the_parse(b1reg):
    with pytest.raises(T.Refused) as err:
        b1reg.decode(build_pdf(["x"] * 3), name="s.pdf", limits=T.Limits(max_seconds=0.0))
    assert err.value.reason == "timeout"
    import sys
    assert sys.gettrace() is None or "tracer" not in getattr(sys.gettrace(), "__name__", "")


@needs_pypdf
def test_pdf_damaged_is_named_error(b1reg):
    with pytest.raises(T.ArtifactError):
        b1reg.decode(b"%PDF-1.7\n" + b"\x00garbage" * 100, name="d.pdf")


def test_pdf_without_pypdf_gives_header_facts(b1reg, monkeypatch):
    real = importlib.util.find_spec
    monkeypatch.setattr(pdfmod.importlib.util, "find_spec", lambda name, *a: None if name == "pypdf" else real(name, *a))
    with pytest.raises(T.Unsupported) as err:
        b1reg.decode(build_pdf(["a", "b", "c"]), name="x.pdf")
    assert err.value.needs == ("pypdf",)
    assert err.value.facts["version"] == "1.4" and err.value.facts["pages"] == 3 and not err.value.facts["encrypted"]
    assert "3 page(s) declared" in str(err.value)


def test_header_facts_encrypt_flag():
    data = build_pdf(["a"]).replace(b"/Root 1 0 R", b"/Root 1 0 R /Encrypt 9 0 R")
    assert pdfmod.header_facts(data)["encrypted"]
