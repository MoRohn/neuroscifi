"""PNG unfiltering holds its true working set (fix lane F1, ruling A.2; code review H1): the Average/Paeth
reconstruction works in bands of rows, the pixel array is the only whole-image copy, and `working_set` (what is
charged against max_decoded_bytes BEFORE anything is allocated) is not exceeded. The reference unfilter below is
written from the PNG specification (section 9), pixel by pixel, independently of the adapter."""
import struct
import tracemalloc
import zlib

import numpy as np
import pytest

from neuroscifi.artifacts import adapters
from neuroscifi.artifacts.adapters import png as P
from neuroscifi.artifacts.safe import Deadline
from neuroscifi.artifacts.types import Limits, Refused


def chunk(kind, body):
    return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))


def png_bytes(raw, width, height, ctype=2):
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, ctype, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


def reference_unfilter(raw, h, rowbytes, bpp):
    out, prev = [], [0] * rowbytes
    for y in range(h):
        t, row, cur = raw[y * (rowbytes + 1)], raw[y * (rowbytes + 1) + 1:(y + 1) * (rowbytes + 1)], []
        for i, v in enumerate(row):
            a = cur[i - bpp] if i >= bpp else 0
            b = prev[i]
            c = prev[i - bpp] if i >= bpp else 0
            if t == 0:
                pred = 0
            elif t == 1:
                pred = a
            elif t == 2:
                pred = b
            elif t == 3:
                pred = (a + b) // 2
            else:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pred = a if pa <= pb and pa <= pc else (b if pb <= pc else c)
            cur.append((v + pred) & 0xFF)
        out.append(cur)
        prev = cur
    return np.array(out, np.uint8)


@pytest.mark.parametrize("band_bytes", [64, 1000, 8 << 20])
def test_every_filter_against_the_reference_in_bands(monkeypatch, band_bytes):
    monkeypatch.setattr(P, "BAND_BYTES", band_bytes)
    rng = np.random.default_rng(7)
    h, w, bpp = 23, 17, 3
    rows = [bytes([y % 5]) + rng.integers(0, 256, w * bpp, dtype=np.uint8).tobytes() for y in range(h)]
    raw = b"".join(rows)
    got = P.unfilter(raw, h, w * bpp, bpp, Deadline(10))
    assert np.array_equal(got, reference_unfilter(raw, h, w * bpp, bpp))


def test_the_peak_stays_within_what_is_charged():
    h, w = 600, 800                        # Paeth on every row
    raw = (b"\x04" + bytes(range(256)) * (w * 3 // 256) + bytes(w * 3 % 256)) * h
    data = png_bytes(raw, w, h)
    charged = P.working_set(w, h, 8, 2, 0, 3)
    tracemalloc.start()
    try:
        decoded = adapters.decode(data, name="p.png")
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert decoded.representation.pixels.shape == (h, w, 3)
    assert peak <= charged + len(data) + (1 << 20), (peak, charged)
    assert peak < 6 * w * h * 3            # the whole-image int32 reconstruction peaked near 10 x


def test_a_tiny_file_declaring_a_huge_image_is_refused_before_allocation():
    width, height = 8000, 5500             # the reviewer's 139 KB all-zero Paeth image
    raw = (b"\x04" + bytes(width * 3)) * height
    data = png_bytes(raw, width, height)
    tracemalloc.start()
    try:
        with pytest.raises(Refused) as caught:
            adapters.decode(data, name="bomb.png", limits=Limits())
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert caught.value.rule == "max_decoded_bytes" and peak < 8 << 20
    assert P.working_set(6000, 5000, 8, 2, 0, 3) <= Limits().max_decoded_bytes   # a 30 MP RGB photo still fits


def test_a_gif_screen_is_charged_before_its_canvas_exists():
    """Security review L1: 65535 x 65535 declared in a 0.1 KB file; the canvas alone would be 16 GiB."""
    data = b"GIF89a" + struct.pack("<HHBBB", 65535, 65535, 0, 0, 0) + b"\x3b"
    tracemalloc.start()
    try:
        with pytest.raises(Refused) as caught:
            adapters.decode(data, name="huge.gif")
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert caught.value.rule == "max_decoded_bytes" and "GIF screen" in str(caught.value) and peak < 8 << 20


def sqlite_bytes(tmp_path, statements):
    import sqlite3
    path = tmp_path / "t.sqlite"
    conn = sqlite3.connect(path)
    for statement in statements:
        conn.execute(statement)
    conn.commit()
    conn.close()
    return path.read_bytes()


def test_a_computed_sqlite_column_is_listed_not_evaluated(tmp_path):
    """Security review H1: 20 virtual columns of printf('%.*c', 1000000, 'x') over 50 rows in an 8 KiB file took
    1.08 GiB. Only stored columns are read now."""
    cols = ", ".join(f"g{i} TEXT GENERATED ALWAYS AS (printf('%.*c', 1000000, 'x')) VIRTUAL" for i in range(20))
    data = sqlite_bytes(tmp_path, [f"CREATE TABLE t(a INTEGER, {cols})"]
                        + [f"INSERT INTO t(a) VALUES({i})" for i in range(50)])
    tracemalloc.start()
    try:
        decoded = adapters.decode(data, name="t.sqlite")
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    member = decoded.representation.members[0]
    assert [c.name for c in member.inner.columns] == ["a"] and member.inner.n_rows == 50
    assert "20 computed column(s) listed, not evaluated" in member.note and peak < 16 << 20


def test_sqlite_cells_are_charged_in_bytes(tmp_path):
    data = sqlite_bytes(tmp_path, ["CREATE TABLE t(a TEXT)"]
                        + ["INSERT INTO t VALUES(printf('%.*c', 100000, 'x'))" for _ in range(40)])
    decoded = adapters.decode(data, name="t.sqlite", limits=Limits(max_decoded_bytes=1 << 20))
    member = decoded.representation.members[0]
    assert member.note.startswith("stopped early (byte limit)") and member.inner.n_rows < 11


def ods_bytes(rows, cols, text):
    import io
    import zipfile
    table = (f'<table:table table:name="S"><table:table-row table:number-rows-repeated="{rows}"><table:table-cell '
             f'table:number-columns-repeated="{cols}"><text:p>{text}</text:p></table:table-cell></table:table-row>'
             '</table:table>')
    content = ('<?xml version="1.0"?><office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:'
               'office:1.0" xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0" xmlns:text="urn:oasis:'
               'names:tc:opendocument:xmlns:text:1.0"><office:body><office:spreadsheet>' + table
               + '</office:spreadsheet></office:body></office:document-content>')
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        z.writestr("mimetype", "application/vnd.oasis.opendocument.spreadsheet")
        z.writestr("content.xml", content, compress_type=zipfile.ZIP_DEFLATED)
    return out.getvalue()


def test_a_repeated_ods_value_is_charged_per_repetition():
    """Security review H2: one 100 000-character cell repeated over 4900 x 1000 cells (983 bytes) ran past 120 s."""
    data = ods_bytes(4900, 1000, "x" * 100_000)
    import time
    started = time.perf_counter()
    decoded = adapters.decode(data, name="t.ods")
    assert time.perf_counter() - started < 5
    assert decoded.partial or "byte limit" in " ".join(decoded.notes + tuple(
        m.note for m in decoded.representation.members))


def xlsx_bytes(n_rows, text):
    import io
    import zipfile
    ns = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
    rel = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    parts = {
        "[Content_Types].xml": '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/'
                               'content-types"><Default Extension="xml" ContentType="application/xml"/><Override '
                               'PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-'
                               'officedocument.spreadsheetml.sheet.main+xml"/></Types>',
        "_rels/.rels": '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/'
                       f'relationships"><Relationship Id="rId1" Type="{rel}/officeDocument" Target="xl/workbook.xml"/>'
                       '</Relationships>',
        "xl/workbook.xml": f'<?xml version="1.0"?><workbook {ns} xmlns:r="{rel}"><sheets><sheet name="S" sheetId="1" '
                           'r:id="rId1"/></sheets></workbook>',
        "xl/_rels/workbook.xml.rels": '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/'
                                      f'package/2006/relationships"><Relationship Id="rId1" Type="{rel}/worksheet" '
                                      'Target="worksheets/sheet1.xml"/></Relationships>',
        "xl/worksheets/sheet1.xml": f'<?xml version="1.0"?><worksheet {ns}><sheetData>' + "".join(
            f'<row r="{i}">' + "".join(f'<c r="{c}{i}" t="s"><v>0</v></c>' for c in "ABCDEFGHIJ") + "</row>"
            for i in range(1, n_rows + 1)) + "</sheetData></worksheet>",
        "xl/sharedStrings.xml": f'<?xml version="1.0"?><sst {ns}><si><t>{text}</t></si></sst>'}
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for name, body in parts.items():
            z.writestr(name, body)
    return out.getvalue()


def test_an_xlsx_shared_string_is_charged_per_reference():
    """Security review H2: one 100 kB shared string referenced by 200 000 cells took 50 s and 2.1 GiB."""
    data = xlsx_bytes(2000, "y" * 100_000)            # 20 000 references x 100 kB = 2 GB of cells
    with pytest.raises(Refused) as caught:
        adapters.decode(data, name="t.xlsx")
    assert caught.value.rule == "max_decoded_bytes"
    small = adapters.decode(xlsx_bytes(20, "honest"), name="t.xlsx")
    assert small.representation.members[0].inner.n_rows in (19, 20)


def test_overlapping_zip_members_are_refused_at_listing():
    """Security review N1: many directory entries naming one member's bytes are refused before any inflation."""
    import io
    import zipfile
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("a.txt", "x" * 1000)
    data = out.getvalue()
    eocd = data.rfind(b"PK\x05\x06")
    cd_size, cd_off = struct.unpack("<LL", data[eocd + 12:eocd + 20])
    central = data[cd_off:cd_off + cd_size]
    copies = [central.replace(b"a.txt", f"{c}.txt".encode()) for c in "bcdefghij"]
    new_cd = central + b"".join(copies)
    n = 1 + len(copies)
    forged = (data[:cd_off] + new_cd + data[eocd:eocd + 8] + struct.pack("<HHLL", n, n, len(new_cd), cd_off)
              + data[eocd + 20:])
    for name in ("t.zip", "t.docx"):
        with pytest.raises(Refused) as caught:
            adapters.decode(forged, name=name)
        assert caught.value.rule == "overlapping_members"
    assert adapters.decode(data, name="t.zip").representation.members[0].size == 1000


def test_structure_is_charged_before_the_parser_runs():
    data = b"".join(b"[s%d]\n" % i for i in range(200_000))        # 200 000 sections: about 400 MiB in configparser
    with pytest.raises(Refused) as caught:
        adapters.decode(data, name="big.ini")
    assert caught.value.rule == "max_decoded_bytes" and "MiB" in str(caught.value)
    assert adapters.decode(b"[a]\nx = 1\n", name="small.ini").representation.kind == "tree"


def test_structure_hidden_past_the_detection_prefix_is_refused():
    """Security review L4: 200 000 nested <a> named .xml was read quietly as plain text."""
    deep = b"<a>" * 200_000 + b"x" + b"</a>" * 200_000
    with pytest.raises(Refused) as caught:
        adapters.decode(deep, name="deep.xml")
    assert caught.value.rule == "unconfirmed_structure"
    assert adapters.decode(b"word " * 20_000, name="long.txt").representation.kind == "document"
    assert adapters.decode(b"<a>" * 30 + b"</a>" * 30, name="small.xml").detected.format == "xml"
