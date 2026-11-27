"""Lane FX2 (ADR-080 rules 2 and 3, the outcome table in `artifacts/outcome.py`): a reference to something outside
the file is never followed and never refuses by itself; embedded active content is never run and never refuses by
itself. Each case is the security reviewer's third-pass reproduction (finding F3-1: `review1/s/in5/gltf_ext*.gltf`,
`s/in7/xlsx_extlink.xlsx`, `docx_macro.docx`, `kml_netlink.kml`, `pdf_js.pdf`, `svg_ext.svg`), rebuilt here byte for
byte where the format allows (ZIP packages are rebuilt from the same members): each is answered WITH its note, the
note never quotes the address, nothing is opened, connected to or started while it is read, and the parser-level
attack beside each (an entity, a DTD, an XInclude, a lying size) is still refused whole (rule 1)."""
from __future__ import annotations

import base64
import builtins
import io
import json
import os
import socket
import struct
import subprocess
import zipfile

import pytest

from neuroscifi.artifacts import adapters, outcome
from neuroscifi.artifacts import types as T

REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
RELS_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
ADDRESSES = ("passwd", "127.0.0.1", "t.dotm", "a.kml", "calc", "x.png", "x.svg", "example")


@pytest.fixture
def nothing_leaves(monkeypatch):
    """Every way a reader could fetch or run something fails the test: a connection, a process, opening a path the
    file names (opens of other paths, e.g. a lazy import, are allowed and recorded)."""
    opened: list[str] = []
    real_open = builtins.open

    def no(*args, **kwargs):
        raise AssertionError(f"a reader tried to fetch or run something: {args[:1]}")

    def watched_open(file, *args, **kwargs):
        opened.append(str(file))
        if any(a in str(file) for a in ADDRESSES):
            no(file)
        return real_open(file, *args, **kwargs)

    for target, name in ((socket.socket, "connect"), (socket, "create_connection"), (subprocess, "Popen"),
                         (os, "system"), (os, "execv"), (os, "execvp"), (os, "posix_spawn")):
        monkeypatch.setattr(target, name, no)
    monkeypatch.setattr(builtins, "open", watched_open)
    return opened


def _zip(members: list[tuple[str, bytes]]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, body in members:
            z.writestr(name, body)
    return buf.getvalue()


def _rels(*rels: str) -> bytes:
    return (f'<?xml version="1.0"?><Relationships xmlns="{RELS_NS}">' + "".join(rels) + "</Relationships>").encode()


def _types(part: str, kind: str) -> bytes:
    return ('<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="xml" ContentType="application/xml"/><Override PartName="/' + part + '" ContentType="'
            'application/vnd.openxmlformats-officedocument.' + kind + '.main+xml"/></Types>').encode()


def _answered(data: bytes, name: str):
    out = adapters.decode(data, name=name)
    assert outcome.of_decoded(out) == outcome.ANSWERED, out.notes
    assert not any(a in n for n in out.notes for a in ADDRESSES), out.notes       # what refers, never where
    return out


# ---------------------------------------------------------------- glTF: an outside buffer (gltf_extfile/extrel/exthttp)

def _gltf(uri: str) -> bytes:
    return json.dumps({"asset": {"version": "2.0"}, "buffers": [{"byteLength": 36, "uri": uri}],
                       "bufferViews": [{"buffer": 0, "byteOffset": 0, "byteLength": 36}],
                       "accessors": [{"bufferView": 0, "componentType": 5126, "count": 3, "type": "VEC3",
                                      "max": [1, 1, 1], "min": [0, 0, 0]}],
                       "meshes": [{"primitives": [{"attributes": {"POSITION": 0}}]}], "nodes": [{"mesh": 0}],
                       "scenes": [{"nodes": [0]}], "scene": 0}).encode()


@pytest.mark.parametrize("uri", ["file:///etc/passwd", "../../etc/passwd", "http://127.0.0.1:9/b.bin"])
def test_gltf_outside_buffer_is_named_and_its_geometry_is_cannot(uri, nothing_leaves):
    # before lane FX2: answered as a mesh of 0 vertices and 0 triangles (a false answer about its geometry)
    with pytest.raises(T.HeaderOnly) as err:
        adapters.decode(_gltf(uri), name="m.gltf")
    assert outcome.of_error(err.value) == outcome.CANNOT
    assert str(err.value).startswith(outcome.outside("buffer 0, which holds its geometry"))
    assert err.value.facts["meshes"] == 1 and err.value.facts["accessors"] == 1
    assert not any(a in str(err.value) + json.dumps(err.value.facts) for a in ADDRESSES)


def test_gltf_lying_embedded_buffer_is_still_refused():
    blob = base64.b64encode(struct.pack("<9f", *range(9))).decode()
    doc = json.loads(_gltf("data:application/octet-stream;base64," + blob))
    doc["buffers"][0]["byteLength"] = 1 << 40                      # declares more than any limit allows
    with pytest.raises(T.ArtifactError) as err:
        adapters.decode(json.dumps(doc).encode(), name="m.gltf")
    assert outcome.of_error(err.value) == outcome.REFUSED


# ------------------------------------------------- XLSX: a sheet outside the workbook (xlsx_extlink), formula without cache

def _xlsx(sheet_rel: str, sheet: bytes, extra=()) -> bytes:
    return _zip([("[Content_Types].xml", _types("xl/workbook.xml", "spreadsheetml.sheet")),
                 ("_rels/.rels", _rels(f'<Relationship Id="rId1" Type="{REL}/officeDocument" '
                                       'Target="xl/workbook.xml"/>')),
                 ("xl/workbook.xml", b'<?xml version="1.0"?><workbook xmlns="http://schemas.openxmlformats.org/'
                                     b'spreadsheetml/2006/main" xmlns:r="' + REL.encode() + b'"><sheets><sheet name="S"'
                                     b' sheetId="1" r:id="rId1"/></sheets></workbook>'),
                 ("xl/_rels/workbook.xml.rels", _rels(sheet_rel)),
                 ("xl/worksheets/sheet1.xml", sheet), *extra])


SHEET = (b'<?xml version="1.0"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
         b'<sheetData/></worksheet>')


def test_xlsx_outside_sheet_is_named_not_a_member(nothing_leaves):
    data = _xlsx(f'<Relationship Id="rId1" Type="{REL}/worksheet" Target="file:///etc/passwd" TargetMode="External"/>',
                 SHEET)
    out = _answered(data, "x.xlsx")
    assert outcome.outside("1 x sheet") in out.notes
    assert "sheet 1 lives outside the workbook: not fetched, its cells cannot be known here" in out.notes
    assert out.representation.members == ()                       # nothing answered about the outside sheet


def test_xlsx_formula_linking_another_workbook_without_cache_is_not_known(nothing_leaves):
    sheet = (b'<?xml version="1.0"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
             b'<sheetData><row r="1"><c r="A1" t="str"><v>total</v></c><c r="B1"><f>[1]Other!A1</f></c></row>'
             b'<row r="2"><c r="A2"><v>3</v></c><c r="B2"><v>4</v></c></row></sheetData></worksheet>')
    link = (f'<Relationship Id="rId9" Type="{REL}/externalLinkPath" Target="http://127.0.0.1:9/other.xlsx" '
            'TargetMode="External"/>')
    data = _xlsx(f'<Relationship Id="rId1" Type="{REL}/worksheet" Target="worksheets/sheet1.xml"/>', sheet,
                 extra=[("xl/externalLinks/externalLink1.xml", b'<?xml version="1.0"?><externalLink xmlns="http://'
                         b'schemas.openxmlformats.org/spreadsheetml/2006/main"/>'),
                        ("xl/externalLinks/_rels/externalLink1.xml.rels", _rels(link))])
    out = _answered(data, "x.xlsx")
    assert outcome.outside("1 x linked workbook") in out.notes
    member = out.representation.members[0]
    texts = list(out.notes) + [str(member.note)] + list(getattr(member.inner, "notes", ()) or ())
    assert any("1 have no cached value: what they hold cannot be known here" in t for t in texts), texts


def test_xlsx_entity_beside_is_still_refused():
    sheet = (b'<?xml version="1.0"?><!DOCTYPE w [<!ENTITY x SYSTEM "file:///etc/hostname">]><worksheet xmlns="http'
             b'://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData/></worksheet>')
    with pytest.raises(T.Refused):
        adapters.decode(_xlsx(f'<Relationship Id="rId1" Type="{REL}/worksheet" Target="worksheets/sheet1.xml"/>',
                              sheet), name="x.xlsx")


# ------------------------------------------------------------- DOCX: a macro project and a remote template (docx_macro)

def _docx(extra=(), rels=None) -> bytes:
    doc = (b'<?xml version="1.0"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
           b'<w:body><w:p><w:r><w:t>hi</w:t></w:r></w:p></w:body></w:document>')
    members = [("[Content_Types].xml", _types("word/document.xml", "wordprocessingml.document")),
               ("_rels/.rels", _rels(f'<Relationship Id="rId1" Type="{REL}/officeDocument" '
                                     'Target="word/document.xml"/>')),
               ("word/document.xml", doc), *extra]
    if rels is not None:
        members.append(("word/_rels/document.xml.rels", rels))
    return _zip(members)


def test_docx_macro_and_remote_template_are_named_and_its_words_read(nothing_leaves):
    data = _docx([("word/vbaProject.bin", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 996)],
                 _rels(f'<Relationship Id="r1" Type="{REL}/attachedTemplate" Target="http://127.0.0.1:9/t.dotm" '
                       'TargetMode="External"/>'))
    out = _answered(data, "d.docx")
    assert outcome.active("a macro project") in out.notes and outcome.outside("1 x template") in out.notes
    assert out.representation.text().strip() == "hi"


def test_docx_entity_in_an_unread_part_is_still_refused():
    part = (b'<?xml version="1.0"?><!DOCTYPE r [<!ENTITY x SYSTEM "file:///etc/hostname">]><r>&x;</r>')
    with pytest.raises(T.Refused):
        adapters.decode(_docx([("customXml/item1.xml", part)]), name="d.docx")


# ------------------------------------------------------------------------------------- KML: a NetworkLink (kml_netlink)

KML_NETLINK = (b'<?xml version="1.0"?><kml xmlns="http://www.opengis.net/kml/2.2"><NetworkLink><Link><href>http://'
               b'127.0.0.1:9/a.kml</href></Link></NetworkLink><Placemark><Point><coordinates>0,0</coordinates></Point>'
               b'</Placemark></kml>')


def test_kml_network_link_is_named_once_and_its_features_read(nothing_leaves):
    out = _answered(KML_NETLINK, "k.kml")
    assert outcome.outside("1 link(s) to other documents or images") in out.notes
    assert [f.geometry for f in out.representation.features] == ["Point"]


def test_kml_entity_is_still_refused():
    with pytest.raises(T.Refused):
        adapters.decode(b'<?xml version="1.0"?><!DOCTYPE kml [<!ENTITY x SYSTEM "file:///etc/hostname">]><kml xmlns='
                        b'"http://www.opengis.net/kml/2.2"><Placemark><name>&x;</name></Placemark></kml>', name="k.kml")


# --------------------------------------------------------------------- PDF: a JavaScript OpenAction and a Launch (pdf_js)

def _pdf(catalog: bytes) -> bytes:
    objs = [b"<< /Type /Catalog /Pages 2 0 R " + catalog + b" >>", b"<< /Type /Pages /Kids [] /Count 0 >>"]
    out, offsets = b"%PDF-1.7\n", []
    for i, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1) + b"".join(b"%010d 00000 n \n" % o for o in offsets)
    return out + b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)


@pytest.mark.parametrize("catalog,active,outside", [
    (b"/OpenAction << /S /JavaScript /JS (app.alert(1)) >> /AA << /O << /S /Launch /F (calc.exe) >> >>",
     "a launch action (it would start a program), a script and an auto-run action", None),
    (b"/OpenAction << /S /J#61vaScript /J#53 (app.alert(1)) >>", "a script and an auto-run action", None),
    (b"/Names << /EmbeddedFiles << /Names [] >> >>", "an embedded file", None),
    (b"/OpenAction << /S /URI /URI (http://127.0.0.1:9/) >>", "an auto-run action", "link address"),
    (b"/OpenAction [2 0 R /Fit]", None, None),                     # a view to open at: not an action, nothing named
])
def test_pdf_active_content_is_named_and_not_run(catalog, active, outside, nothing_leaves):
    pytest.importorskip("pypdf")
    out = _answered(_pdf(catalog), "p.pdf")
    assert [n for n in out.notes if outcome.active("")[:13] in n and n.endswith(": not run")] == (
        [outcome.active(active)] if active else [])
    assert [n for n in out.notes if outcome.OUTSIDE in n] == ([outcome.outside(outside)] if outside else [])


# --------------------------------------------------------------------------- SVG: outside images and a use (svg_ext)

SVG_EXT = (b'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink"><image xlink:href="file:'
           b'///etc/passwd"/><image href="http://127.0.0.1:9/x.png"/><use href="http://127.0.0.1:9/x.svg#a"/><style>'
           b'@import url(http://127.0.0.1:9/a.css);</style><script>alert(1)</script></svg>')


def test_svg_outside_references_are_named_and_its_script_is_not_kept(nothing_leaves):
    out = _answered(SVG_EXT, "s.svg")
    assert outcome.outside("2 x image, 1 x use target") in out.notes
    assert "refused and not kept: 1 x script" in out.notes        # as lane FX left it
    assert [s.kind for s in out.representation.shapes] == ["image", "image"]


def test_svg_entity_and_xinclude_are_still_refused():
    for data in (b'<?xml version="1.0"?><!DOCTYPE svg [<!ENTITY a "x">]><svg xmlns="http://www.w3.org/2000/svg">'
                 b'<text>&a;</text></svg>',
                 b'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xi="http://www.w3.org/2001/XInclude"><xi:include '
                 b'href="/etc/passwd"/></svg>'):
        with pytest.raises(T.Refused):
            adapters.decode(data, name="s.svg")


def test_the_table_states_the_rule():
    # parser-level attacks refuse the whole file; references and active content are not refusal rules at all
    assert outcome.refuses_whole("xml_dtd") and outcome.refuses_whole("external_reference")
    assert outcome.outside("x") == "the file refers to a file or address outside itself: not fetched (x)"
    assert outcome.active("a macro project") == "the file holds a macro project: not run"
