"""The store's text layer and its forget-by-text scan see the words of every representation the REAL registry
returns (integration lane, item 2; ADR-072's ruling on lane B2's deviations): words a B2 representation carries in its
`words` field (a PNG text chunk, a WAV LIST chunk), words of B1's documents and sheets (a DOCX paragraph, an XLSX
cell), a ZIP member's words (a Bundle), and the words of a format that is only identified and whose header is read
(an Exif description in a JPEG: `HeaderOnly`). Each file is assembled here by hand with struct, zlib and zipfile, and
the root's name sits only inside it."""
import io
import struct
import zipfile
import zlib
from contextlib import closing

import pytest

from neuroscifi.artifacts import store as A
from neuroscifi.instance import evidence, governance, store

T0 = 1_800_000_000.0
ROOT = "Wilhelmina-kv3"
SENTENCE = f"Met {ROOT} at the harbour."


def _chunk(kind: bytes, body: bytes) -> bytes:
    return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))


def png_with_text() -> bytes:
    ihdr = struct.pack(">IIBBBBB", 2, 1, 8, 0, 0, 0, 0)                       # 2x1 gray, 8 bit
    return (b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", ihdr) + _chunk(b"tEXt", b"Comment\x00" + SENTENCE.encode())
            + _chunk(b"IDAT", zlib.compress(b"\x00\x10\x20")) + _chunk(b"IEND", b""))


def wav_with_list() -> bytes:
    fmt = struct.pack("<HHIIHH", 1, 1, 8000, 16000, 2, 16)
    samples = struct.pack("<8h", 0, 1000, 2000, 1000, 0, -1000, -2000, -1000)
    comment = SENTENCE.encode() + b"\x00"
    info = b"INFO" + b"ICMT" + struct.pack("<I", len(comment)) + comment + (b"\x00" if len(comment) % 2 else b"")
    body = (b"WAVE" + b"fmt " + struct.pack("<I", len(fmt)) + fmt + b"data" + struct.pack("<I", len(samples))
            + samples + b"LIST" + struct.pack("<I", len(info)) + info)
    return b"RIFF" + struct.pack("<I", len(body)) + body


def _zip(members) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for name, data in members:
            z.writestr(zipfile.ZipInfo(name, (2020, 1, 1, 0, 0, 0)), data, compress_type=zipfile.ZIP_DEFLATED)
    return out.getvalue()


W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
S = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" ' \
    'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
CT = (b'<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
      b'<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.'
      b'wordprocessingml.document.main+xml"/><Override PartName="/xl/workbook.xml" ContentType="application/'
      b'vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/></Types>')


def docx_with_paragraph() -> bytes:
    doc = (f'<?xml version="1.0"?><w:document {W}><w:body><w:p><w:r><w:t>{SENTENCE}</w:t></w:r></w:p>'
           '</w:body></w:document>').encode()
    return _zip([("[Content_Types].xml", CT), ("word/document.xml", doc)])


def xlsx_with_cell() -> bytes:
    wb = f'<?xml version="1.0"?><workbook {S}><sheets><sheet name="S" sheetId="1" r:id="rId1"/></sheets></workbook>'
    rels = ('<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="worksheet" Target="worksheets/sheet1.xml"/></Relationships>')
    sheet = (f'<?xml version="1.0"?><worksheet {S}><sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>who</t></is>'
             f'</c></row><row r="2"><c r="A2" t="inlineStr"><is><t>{SENTENCE}</t></is></c></row></sheetData>'
             '</worksheet>')
    return _zip([("[Content_Types].xml", CT), ("xl/workbook.xml", wb.encode()),
                 ("xl/_rels/workbook.xml.rels", rels.encode()), ("xl/worksheets/sheet1.xml", sheet.encode())])


def zip_with_member() -> bytes:
    return _zip([("notes/today.txt", (SENTENCE + "\n").encode()), ("other.txt", b"nothing here\n")])


def jpeg_with_exif() -> bytes:
    text = SENTENCE.encode() + b"\x00"
    ifd = struct.pack("<H", 1) + struct.pack("<HHII", 270, 2, len(text), 8 + 2 + 12 + 4) + struct.pack("<I", 0)
    tiff = b"II*\x00" + struct.pack("<I", 8) + ifd + text

    def seg(marker, body):
        return bytes((0xFF, marker)) + struct.pack(">H", len(body) + 2) + body
    sof = struct.pack(">BHHB", 8, 2, 3, 3) + b"\x01\x11\x00\x02\x11\x00\x03\x11\x00"
    return b"\xff\xd8" + seg(0xE1, b"Exif\x00\x00" + tiff) + seg(0xC0, sof) + b"\xff\xd9"


FILES = {"png text chunk": (png_with_text, "p.png", "raster"),
         "wav LIST chunk": (wav_with_list, "s.wav", "signal"),
         "docx paragraph": (docx_with_paragraph, "d.docx", "document"),
         "xlsx cell": (xlsx_with_cell, "b.xlsx", "bundle"),
         "zip member": (zip_with_member, "a.zip", "bundle"),
         "jpeg exif (header only)": (jpeg_with_exif, "j.jpg", None)}


@pytest.fixture
def individual(tmp_path):
    path = tmp_path / "individual.sqlite"
    db = store.create(path, T0)
    try:
        yield db, path
    finally:
        db.close()


@pytest.mark.parametrize("case", list(FILES))
def test_the_text_layer_holds_the_words_of_every_representation(individual, case):
    db, _ = individual
    build, name, kind = FILES[case]
    record = A.add(db, build(), name=name, now=T0)
    shown = A.show(db, record.id)
    if kind is None:                                   # identified, header read, content not decoded
        error = shown["index"]["error"]
        assert error["header_only"] is True and error["facts"]["width"] == 3 and "words" not in error
    else:
        assert shown["index"]["representation"]["kind"] == kind
    assert SENTENCE in A.text_for_model(db, record.id)
    assert ROOT not in str(shown["index"])             # the words are in the text layer, not in the index


def test_forgetting_a_root_removes_each_family_that_names_it_in_its_words(individual):
    db, path = individual
    evidence.start_epoch(db, "e1", T0)
    evidence.observe(db, {"id": "ev1", "epoch": "e1", "channel": "word", "action": 0, "bit": 1, "root": ROOT}, T0)
    named = {case: A.add(db, build(), name=name, now=T0).id for case, (build, name, _) in FILES.items()}
    kept = A.add(db, b"a note about nobody\n", name="plain.txt", now=T0).id

    result = governance.forget_partner(db, ROOT, T0 + 1)

    assert result["status"] == "forgotten"
    assert result["effects"]["artifacts_removed"] == len(named)
    held = {i for (i,) in db.execute("SELECT id FROM artifacts")}
    assert held == {kept}
    with closing(A._sidecar(db, create=False)) as side:
        assert all(side.layer(i, "text") is None for i in named.values())
