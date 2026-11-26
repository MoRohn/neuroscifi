"""Lane B1 office packages: DOCX, ODT, PPTX, ODP, EPUB assembled by hand in the test, plus hostile variants."""
import pytest

from neuroscifi.artifacts import types as T

import _b1_support
from _b1_support import make_zip

b1reg = _b1_support.b1reg      # the fixture

CT = b'<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>'
WNS = ('xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
       'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
       'xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" '
       'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"')
DOC = f"""<?xml version="1.0" encoding="UTF-8"?><w:document {WNS}><w:body>
<w:p><w:pPr><w:pStyle w:val="Kop1"/></w:pPr><w:r><w:t>Introduction</w:t></w:r></w:p>
<w:p><w:r><w:t xml:space="preserve">Plain </w:t></w:r><w:del><w:r><w:delText>gone</w:delText></w:r></w:del>
<w:ins><w:r><w:t>kept</w:t></w:r></w:ins><w:r><w:fldChar/><w:instrText>PAGE</w:instrText><w:t>3</w:t></w:r></w:p>
<w:p><w:pPr><w:numPr><w:ilvl w:val="1"/><w:numId w:val="1"/></w:numPr></w:pPr><w:r><w:t>sub item</w:t></w:r></w:p>
<w:tbl><w:tr><w:tc><w:p><w:r><w:t>name</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>n</w:t></w:r></w:p></w:tc></w:tr>
<w:tr><w:tc><w:p><w:r><w:t>x</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>4</w:t></w:r></w:p></w:tc></w:tr></w:tbl>
<w:p><w:r><w:drawing><wp:inline><wp:docPr id="1" name="p" descr="A red square"/><a:graphic><a:graphicData>
<a:blip r:embed="rId5"/></a:graphicData></a:graphic></wp:inline></w:drawing></w:r></w:p>
<w:sectPr/></w:body></w:document>""".encode()
RELS = (b'<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        b'<Relationship Id="rId5" Type="image" Target="media/image1.png"/></Relationships>')
STYLES = (f'<?xml version="1.0"?><w:styles {WNS}><w:style w:type="paragraph" w:styleId="Kop1">'
          '<w:name w:val="heading 1"/></w:style></w:styles>').encode()
CORE = (b'<?xml version="1.0"?><cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/'
        b'metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>Report</dc:title>'
        b'</cp:coreProperties>')


def docx(document=DOC, extra=()):
    return make_zip([("[Content_Types].xml", CT), ("word/document.xml", document),
                     ("word/_rels/document.xml.rels", RELS), ("word/styles.xml", STYLES),
                     ("docProps/core.xml", CORE), ("word/media/image1.png", b"\x89PNG\r\n\x1a\n")] + list(extra))


def test_docx(b1reg):
    out = b1reg.decode(docx(), name="r.docx")
    assert out.detected.format == "docx"
    doc = out.representation
    assert doc.title == "Report"
    assert [(b.kind, b.level, b.text) for b in doc.blocks if b.kind != "table" and b.kind != "figure"] == [
        ("heading", 1, "Introduction"), ("paragraph", 0, "Plain kept3"), ("list_item", 2, "sub item")]
    t = next(b for b in doc.blocks if b.kind == "table").ref
    assert [c.name for c in t.columns] == ["name", "n"] and list(t.columns[1].values) == [4]
    fig = next(b for b in doc.blocks if b.kind == "figure")
    assert fig.ref == {"alt": "A red square", "caption": None, "member": "word/media/image1.png", "address": None}


def test_docx_with_dtd_is_refused(b1reg):
    bomb = (b'<?xml version="1.0"?><!DOCTYPE d [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;&a;">]>'
            b'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
            b'<w:p><w:r><w:t>&b;</w:t></w:r></w:p></w:body></w:document>')
    with pytest.raises(T.Refused) as err:
        b1reg.decode(docx(bomb), name="bomb.docx")
    assert err.value.rule == "xml_dtd"


def test_docx_macros_reported_not_read(b1reg):
    out = b1reg.decode(docx(extra=[("word/vbaProject.bin", b"\xd0\xcf\x11\xe0" + b"\x00" * 60)]), name="m.docm")
    assert any("macros" in n for n in out.notes)


def test_docx_inflation_bomb_is_refused(b1reg):
    big = b"<w:document " + WNS.encode() + b"><w:body><w:p><w:r><w:t>" + b"a" * 3_000_000 + b"</w:t></w:r></w:p>" \
          b"</w:body></w:document>"
    with pytest.raises(T.Refused) as err:
        b1reg.decode(docx(big), name="z.docx", limits=T.Limits(max_ratio=50))
    assert err.value.rule == "max_ratio"


ODF = ('xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
       'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" '
       'xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0" '
       'xmlns:draw="urn:oasis:names:tc:opendocument:xmlns:drawing:1.0" '
       'xmlns:xlink="http://www.w3.org/1999/xlink" '
       'xmlns:svg="urn:oasis:names:tc:opendocument:xmlns:svg-compatible:1.0" '
       'xmlns:presentation="urn:oasis:names:tc:opendocument:xmlns:presentation:1.0"')


def odf(mime, content, meta=None):
    members = [("mimetype", mime.encode()), ("content.xml", content.encode())]
    if meta:
        members.append(("meta.xml", meta.encode()))
    return make_zip(members, method=0)


def test_odt(b1reg):
    content = f"""<?xml version="1.0"?><office:document-content {ODF}><office:body><office:text>
<text:h text:outline-level="2">Methods</text:h>
<text:p>Two<text:s text:c="2"/>spaces<text:tab/>tab<text:note><text:note-body><text:p>foot</text:p></text:note-body></text:note></text:p>
<text:list><text:list-item><text:p>outer</text:p><text:list><text:list-item><text:p>inner</text:p></text:list-item></text:list></text:list-item></text:list>
<table:table table:name="T1"><table:table-row><table:table-cell><text:p>k</text:p></table:table-cell><table:table-cell><text:p>v</text:p></table:table-cell></table:table-row>
<table:table-row><table:table-cell><text:p>a</text:p></table:table-cell><table:table-cell><text:p>9</text:p></table:table-cell></table:table-row>
<table:table-row table:number-rows-repeated="1048000"><table:table-cell table:number-columns-repeated="1024"/></table:table-row></table:table>
<text:p><draw:frame><draw:image xlink:href="Pictures/p.png"/><svg:title>Plot</svg:title></draw:frame></text:p>
</office:text></office:body></office:document-content>"""
    meta = ('<?xml version="1.0"?><office:document-meta xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
            'xmlns:dc="http://purl.org/dc/elements/1.1/"><office:meta><dc:title>Lab</dc:title></office:meta>'
            '</office:document-meta>')
    out = b1reg.decode(odf("application/vnd.oasis.opendocument.text", content, meta), name="a.odt")
    assert out.detected.format == "odt"
    doc = out.representation
    assert doc.title == "Lab"
    assert doc.blocks[0].kind == "heading" and doc.blocks[0].level == 2 and doc.blocks[0].text == "Methods"
    assert doc.blocks[1].text == "Two  spaces\ttab"      # text:s c=2 is two spaces
    assert [(b.level, b.text) for b in doc.blocks if b.kind == "list_item"] == [(1, "outer"), (2, "inner")]
    t = next(b for b in doc.blocks if b.kind == "table").ref
    assert t.n_rows == 1 and list(t.columns[1].values) == [9]
    fig = next(b for b in doc.blocks if b.kind == "figure")
    assert fig.ref["member"] == "Pictures/p.png" and fig.ref["caption"] == "Plot"
    assert "foot" not in doc.text()


PNS = ('xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
       'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
       'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"')


def slide(title, bullets, pic=False):
    sp_title = (f'<p:sp><p:nvSpPr><p:cNvPr id="1" name="t"/><p:cNvSpPr/><p:nvPr><p:ph type="title"/></p:nvPr></p:nvSpPr>'
                f'<p:txBody><a:p><a:r><a:t>{title}</a:t></a:r></a:p></p:txBody></p:sp>') if title else ""
    paras = "".join(f'<a:p><a:pPr lvl="{lvl}"/><a:r><a:t>{t}</a:t></a:r></a:p>' for lvl, t in bullets)
    body = (f'<p:sp><p:nvSpPr><p:cNvPr id="2" name="b"/><p:cNvSpPr/><p:nvPr><p:ph idx="1"/></p:nvPr></p:nvSpPr>'
            f'<p:txBody>{paras}</p:txBody></p:sp>')
    picture = ('<p:pic><p:nvPicPr><p:cNvPr id="3" name="pic" descr="Chart of growth"/></p:nvPicPr>'
               '<p:blipFill><a:blip r:embed="rId2"/></p:blipFill></p:pic>') if pic else ""
    return f'<?xml version="1.0"?><p:sld {PNS}><p:cSld><p:spTree>{sp_title}{body}{picture}</p:spTree></p:cSld></p:sld>'.encode()


def test_pptx(b1reg):
    pres = (f'<?xml version="1.0"?><p:presentation {PNS}><p:sldIdLst><p:sldId id="256" r:id="rId1"/>'
            f'<p:sldId id="257" r:id="rId2"/></p:sldIdLst></p:presentation>').encode()
    prels = (b'<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
             b'<Relationship Id="rId1" Type="slide" Target="slides/slide1.xml"/>'
             b'<Relationship Id="rId2" Type="slide" Target="slides/slide2.xml"/></Relationships>')
    s1rels = (b'<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
              b'<Relationship Id="rId2" Type="image" Target="../media/image1.png"/>'
              b'<Relationship Id="rId3" Type="notesSlide" Target="../notesSlides/notesSlide1.xml"/></Relationships>')
    notes = (f'<?xml version="1.0"?><p:notes {PNS}><p:cSld><p:spTree><p:sp><p:nvSpPr><p:cNvPr id="1" name="n"/>'
             f'<p:cNvSpPr/><p:nvPr><p:ph type="body"/></p:nvPr></p:nvSpPr><p:txBody><a:p><a:r><a:t>Say this</a:t>'
             f'</a:r></a:p></p:txBody></p:sp></p:spTree></p:cSld></p:notes>').encode()
    data = make_zip([("[Content_Types].xml", CT), ("ppt/presentation.xml", pres),
                     ("ppt/_rels/presentation.xml.rels", prels),
                     ("ppt/slides/slide1.xml", slide("Growth", [(0, "Up"), (1, "a lot")], pic=True)),
                     ("ppt/slides/_rels/slide1.xml.rels", s1rels),
                     ("ppt/notesSlides/notesSlide1.xml", notes),
                     ("ppt/slides/slide2.xml", slide(None, [(0, "Untitled point")])),
                     ("ppt/media/image1.png", b"\x89PNG\r\n\x1a\n")])
    out = b1reg.decode(data, name="deck.pptx")
    assert out.detected.format == "pptx"
    got = [(b.kind, b.level, b.text) for b in out.representation.blocks]
    assert got == [("heading", 1, "Growth"), ("list_item", 1, "Up"), ("list_item", 2, "a lot"),
                   ("figure", 0, "Chart of growth"), ("note", 0, "Say this"),
                   ("heading", 1, "Slide 2"), ("list_item", 1, "Untitled point")]
    assert out.representation.blocks[3].ref["member"] == "ppt/media/image1.png"
    assert out.representation.blocks[5].ref == {"slide": 2}
    assert any("headed 'Slide N'" in n for n in out.notes)


def test_odp(b1reg):
    content = f"""<?xml version="1.0"?><office:document-content {ODF}><office:body><office:presentation>
<draw:page draw:name="page1"><draw:frame presentation:class="title"><draw:text-box><text:p>Plan</text:p></draw:text-box></draw:frame>
<draw:frame presentation:class="outline"><draw:text-box><text:list><text:list-item><text:p>Step one</text:p></text:list-item></text:list></draw:text-box></draw:frame>
<presentation:notes><draw:frame><draw:text-box><text:p>remember</text:p></draw:text-box></draw:frame></presentation:notes></draw:page>
</office:presentation></office:body></office:document-content>"""
    out = b1reg.decode(odf("application/vnd.oasis.opendocument.presentation", content), name="p.odp")
    assert out.detected.format == "odp"
    assert [(b.kind, b.text) for b in out.representation.blocks] == [("heading", "Plan"), ("list_item", "Step one"),
                                                                     ("note", "remember")]


def test_epub(b1reg):
    container = (b'<?xml version="1.0"?><container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0">'
                 b'<rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>'
                 b'</rootfiles></container>')
    opf = (b'<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" version="3.0">'
           b'<metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>A Book</dc:title></metadata>'
           b'<manifest><item id="c2" href="text/c2.xhtml" media-type="application/xhtml+xml"/>'
           b'<item id="c1" href="text/c1.xhtml" media-type="application/xhtml+xml"/>'
           b'<item id="im" href="images/f.png" media-type="image/png"/></manifest>'
           b'<spine><itemref idref="c1"/><itemref idref="c2"/></spine></package>')
    c1 = b'<html xmlns="http://www.w3.org/1999/xhtml"><body><h1>One</h1><p>First.</p><img src="../images/f.png" alt="Fig"/></body></html>'
    c2 = b'<!DOCTYPE html><html xmlns="http://www.w3.org/1999/xhtml"><body><h1>Two</h1><p>Second.</p></body></html>'
    data = make_zip([("mimetype", b"application/epub+zip"), ("META-INF/container.xml", container),
                     ("OEBPS/content.opf", opf), ("OEBPS/text/c1.xhtml", c1), ("OEBPS/text/c2.xhtml", c2),
                     ("OEBPS/images/f.png", b"\x89PNG\r\n\x1a\n")], method=0)
    out = b1reg.decode(data, name="b.epub")
    assert out.detected.format == "epub"
    doc = out.representation
    assert doc.title == "A Book"
    assert [(b.kind, b.text) for b in doc.blocks if b.kind != "figure"] == [
        ("heading", "One"), ("paragraph", "First."), ("heading", "Two"), ("paragraph", "Second.")]
    fig = next(b for b in doc.blocks if b.kind == "figure")
    assert fig.ref["member"] == "OEBPS/images/f.png" and fig.ref["address"] is None


def test_epub_container_climbing_out_is_malformed(b1reg):
    container = (b'<?xml version="1.0"?><container xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
                 b'<rootfiles><rootfile full-path="../../etc/passwd"/></rootfiles></container>')
    data = make_zip([("mimetype", b"application/epub+zip"), ("META-INF/container.xml", container)], method=0)
    with pytest.raises(T.Malformed):
        b1reg.decode(data, name="x.epub")


def test_too_many_members_refused(b1reg):
    data = make_zip([("[Content_Types].xml", CT), ("word/document.xml", DOC)] + [(f"f{i}", b"") for i in range(50)])
    with pytest.raises(T.Refused) as err:
        b1reg.decode(data, name="r.docx", limits=T.Limits(max_members=10))
    assert err.value.rule == "max_members"
