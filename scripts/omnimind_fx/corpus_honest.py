"""Lane FX's honest corpus at the edges of the new rules (the other direction): honest files each rule might turn
away. Through the benchmark's driver; truth by construction. Expected: right; "abstained" = an honest input turned
away (the cost of a rule). Seeds 43000+."""
from __future__ import annotations

import io
import struct
import zipfile

import numpy as np

from neuroscifi.acceptance.omni import Task, enc
from neuroscifi.acceptance.omni.intake import doc_content, docx_bytes, epub_bytes, pdf_bytes

ITEMS = []
_N = [0]


def add(cls, name, data, question, expect, tol=None):
    _N[0] += 1
    ITEMS.append((cls, Task(f"fx/{cls}/{_N[0]}", "honest", {name: data}, {}, question, expect, tol, "measure")))


def zipf(files, method=zipfile.ZIP_DEFLATED):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", method) as z:
        for n, b in files.items():
            z.writestr(n, b)
    return buf.getvalue()


def build():
    csv_latin = "name,città\nAndré,Zürich\nRené,Genève\n".encode("latin-1")
    add("hint_text_latin1_csv", "people.csv", csv_latin, "table.n_rows", 2)
    xml_latin = '<?xml version="1.0" encoding="ISO-8859-1"?><menu><item>café</item></menu>'.encode("latin-1")
    add("hint_text_latin1_xml", "menu.xml", xml_latin, "tree.value:item", "café")
    html_cp = "<html><body><h1>Caf\xe9</h1><p>na\xefve r\xe9sum\xe9</p></body></html>".encode("cp1252")
    add("hint_text_cp1252_html", "page.html", html_cp, "doc.headings", ["Café"])
    add("pdf_from_encoder", "a.pdf", pdf_bytes("Title", [["one line of text"]]), "detect.format", "pdf")
    add("pdf_header_only_head", "a.pdf", b"%PDF-1.4\n", "detect.format", "pdf")
    add("empty_named_png", "x.png", b"", "detect.format", "png")
    add("empty_named_txt", "x.txt", b"", "detect.format", "text")
    pgm = b"P5\n2 2\n255\n" + bytes([1, 2, 3, 4])
    add("netpbm_trailing_newline", "a.pgm", pgm + b"\n", "image.size", [2, 2])
    add("netpbm_trailing_comment", "a.pgm", pgm + b"# made by hand\n", "image.size", [2, 2])
    frame = b"\xff\xfb\x90\x64" + bytes(413)
    add("mp3_two_frames_no_id3", "a.mp3", frame * 2, "detect.format", "mp3")
    xhtml = (b'<?xml version="1.0"?>\n<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.1//EN" '
             b'"http://www.w3.org/TR/xhtml11/DTD/xhtml11.dtd">\n<html xmlns="http://www.w3.org/1999/xhtml"><head>'
             b'<title>T</title></head><body><h1>Chapter</h1><p>Text.</p></body></html>')
    add("xhtml_public_doctype", "c.html", xhtml, "doc.headings", ["Chapter"])
    ep = epub_bytes("Title", [[(1, "Head", ["one sentence."])]])
    add("epub_plain", "b.epub", ep, "doc.headings", ["Head"])
    d = doc_content(np.random.default_rng(43000), {"sections": 2, "rows": 2})
    docx = docx_bytes(d)
    src = zipfile.ZipFile(io.BytesIO(docx))
    parts = {i.filename: src.read(i.filename) for i in src.infolist()}
    rels = parts["word/_rels/document.xml.rels"].replace(
        b"</Relationships>", b'<Relationship Id="rX" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
        b'relationships/hyperlink" Target="https://example.invalid/" TargetMode="External"/></Relationships>')
    add("docx_with_hyperlink", "h.docx", zipf({**parts, "word/_rels/document.xml.rels": rels}), "doc.headings",
        [h for _, h, _ in d["sections"]])
    add("zip_encrypted_sibling", "e.zip", _encrypted(), "open.csv::table.n_rows", 2)
    broken_png = enc.png(np.zeros((4, 4), np.uint8), mode="gray")[:30]
    add("zip_damaged_inner_png_sibling", "d.zip", zipf({"cut.png": broken_png, "data.csv": b"a,b\n1,2\n3,4\n"}),
        "data.csv::table.n_rows", 2)
    add("zip_xml_with_doctype_sibling", "t.zip", zipf({"tutorial.xml": b'<?xml version="1.0"?><!DOCTYPE r SYSTEM '
                                                       b'"r.dtd"><r/>', "data.csv": b"a,b\n1,2\n3,4\n"}),
        "data.csv::table.n_rows", 2)
    add("markdown_mentions_entity", "notes.md", b"# XML\n\nWrite `<!ENTITY x \"y\">` to declare one.\n\n- a\n- b\n",
        "doc.headings", ["XML"])
    svg_ext = (b'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"><image href="logo.png" width="5" '
               b'height="5"/><rect width="2" height="2"/></svg>')
    add("svg_external_image", "a.svg", svg_ext, "drawing.count:rect", 1)
    psd = (b"8BPS" + struct.pack(">H", 1) + bytes(6) + struct.pack(">HIIHH", 3, 4, 5, 8, 3) + bytes(20))
    add("psd_real_header", "a.psd", psd, "image.size", [5, 4])


def _encrypted():
    data = bytearray(zipf({"secret.csv": b"a,b\n1,2\n", "open.csv": b"a,b\n1,2\n3,4\n"}, zipfile.ZIP_STORED))
    for sig, off in ((b"PK\x03\x04", 6), (b"PK\x01\x02", 8)):
        at = data.find(sig)
        data[at + off] |= 1
    return bytes(data)


build()
