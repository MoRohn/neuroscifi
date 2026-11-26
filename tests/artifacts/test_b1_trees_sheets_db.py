"""Lane B1 trees (XML, plist, TOML, INI, YAML), spreadsheets (XLSX, ODS) and SQLite, against bytes and files the
test builds itself, with hostile variants."""
import math
import sqlite3

import pytest

from neuroscifi.artifacts import types as T
from neuroscifi.artifacts.adapters import sheets, sqlite_db

import _b1_support
from _b1_support import make_zip

b1reg = _b1_support.b1reg      # the fixture


def test_xml_tree_namespaces_mixed_content(b1reg):
    data = (b'<?xml version="1.0"?><r xmlns="urn:x" xmlns:a="urn:a" a:k="v"><item n="1">one</item>'
            b'<item n="2">two<b>bold</b>tail</item><!-- c --></r>')
    out = b1reg.decode(data, name="d.xml")
    assert out.detected.format == "xml"
    root = out.representation.root
    assert root.name == "{urn:x}r" and root.attrs == {"{urn:a}k": "v"}
    assert [c.value for c in root.children] == ["one", "two"] and root.children[0].attrs == {"n": "1"}
    second = root.children[1]
    assert [(c.name, c.value) for c in second.children] == [("{urn:x}b", "bold"), ("#text", "tail")]
    assert "hierarchy" in out.representation.shape().kinds


@pytest.mark.parametrize("body", [
    b'<?xml version="1.0"?><!DOCTYPE r [<!ENTITY e "x">]><r>&e;</r>',
    b'<?xml version="1.0"?><!DOCTYPE r SYSTEM "file:///etc/passwd"><r/>',
    b'<?xml version="1.0"?><!DOCTYPE r [<!ENTITY % p SYSTEM "http://x/">%p;]><r/>'])
def test_xml_dtd_refused(b1reg, body):
    with pytest.raises(T.Refused) as err:
        b1reg.decode(body, name="d.xml")
    assert err.value.rule == "xml_dtd"


def test_xml_depth_and_node_bounds(b1reg):
    with pytest.raises(T.Refused):
        b1reg.decode(b"<x>" * 1000 + b"</x>" * 1000, name="deep.xml")
    with pytest.raises(T.Refused):
        b1reg.decode(b"<r>" + b"<i/>" * 5000 + b"</r>", name="wide.xml", limits=T.Limits(max_elements=1000))


PLIST = b"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict><key>Name</key><string>Demo</string><key>Count</key><integer>3</integer>
<key>Ratio</key><real>0.5</real><key>On</key><true/><key>When</key><date>2024-01-01T00:00:00Z</date>
<key>Blob</key><data>AAEC</data><key>List</key><array><string>a</string><integer>7</integer></array></dict></plist>"""


def test_plist(b1reg):
    out = b1reg.decode(PLIST, name="Info.plist")
    assert out.detected.format == "plist"
    d = out.representation.root.children[0]
    got = {c.name: c.value for c in d.children}
    assert got["Name"] == "Demo" and got["Count"] == 3 and got["Ratio"] == 0.5 and got["On"] is True
    assert got["When"] == "2024-01-01T00:00:00Z" and got["Blob"] is None
    blob = next(c for c in d.children if c.name == "Blob")
    assert blob.attrs == {"type": "data", "base64_chars": "4"}
    assert [c.value for c in next(c for c in d.children if c.name == "List").children] == ["a", 7]
    with pytest.raises(T.Refused):
        b1reg.decode(PLIST.replace(b'PropertyList-1.0.dtd">', b'x.dtd" [<!ENTITY e "x">]>'), name="Info.plist")
    with pytest.raises(T.Unsupported):
        b1reg.decode(b"bplist00" + b"\x00" * 40, name="b.plist")


def test_toml_ini_yaml(b1reg):
    toml = b'title = "x"\n[owner]\nname = "Tom"\ndob = 1979-05-27T07:32:00Z\n[[items]]\nn = 1\n[[items]]\nn = 2\n'
    root = b1reg.decode(toml, name="c.toml").representation.root
    kids = {c.name: c for c in root.children}
    assert kids["title"].value == "x"
    assert {c.name: c.value for c in kids["owner"].children} == {"name": "Tom", "dob": "1979-05-27T07:32:00+00:00"}
    assert [c.children[0].value for c in kids["items"].children] == [1, 2]
    ini = b"[DEFAULT]\nA = 1\n[Server]\nHost = h\npath = %(A)s/x\n"
    out = b1reg.decode(ini, name="s.ini")
    assert out.detected.format == "ini"
    sections = {s.name: {k.name: k.value for k in s.children} for s in out.representation.root.children}
    assert sections["DEFAULT"] == {"A": "1"} and sections["Server"] == {"Host": "h", "path": "%(A)s/x"}
    assert b1reg.decode(b'{"a": [1, 2]}', name="c.yaml").detected.format == "json"     # the structure wins
    from neuroscifi.artifacts.adapters import trees
    yaml_det = T.Detected("tree", "yaml", "application/yaml", "declared", None, ())
    out = trees.read_yaml(b'{"a": [1, 2]}', yaml_det, T.Limits())
    assert out.representation.root.children[0].name == "a" and "JSON subset" in out.notes[-1]
    with pytest.raises(T.Unsupported) as err:
        b1reg.decode(b"a: 1\nb:\n  - x\n", name="c.yaml")
    assert "2 top-level key(s)" in str(err.value) and err.value.needs


SNS = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" ' \
      'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'


def xlsx(sheet_xml, date1904=False):
    wb = (f'<?xml version="1.0"?><workbook {SNS}><workbookPr date1904="{int(date1904)}"/><sheets>'
          f'<sheet name="Data" sheetId="1" r:id="rId1"/><sheet name="Empty" sheetId="2" r:id="rId2"/></sheets></workbook>')
    rels = ('<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="worksheet" Target="worksheets/sheet1.xml"/>'
            '<Relationship Id="rId2" Type="worksheet" Target="worksheets/sheet2.xml"/></Relationships>')
    styles = (f'<?xml version="1.0"?><styleSheet {SNS}><numFmts count="1"><numFmt numFmtId="164" '
              f'formatCode="yyyy\\-mm\\-dd"/></numFmts><cellXfs count="3"><xf numFmtId="0"/><xf numFmtId="14"/>'
              f'<xf numFmtId="164"/></cellXfs></styleSheet>')
    shared = (f'<?xml version="1.0"?><sst {SNS}><si><t>day</t></si><si><t>value</t></si>'
              f'<si><r><t>ri</t></r><r><t>ch</t></r></si></sst>')
    return make_zip([("[Content_Types].xml", b"<Types/>"), ("xl/workbook.xml", wb.encode()),
                     ("xl/_rels/workbook.xml.rels", rels.encode()), ("xl/styles.xml", styles.encode()),
                     ("xl/sharedStrings.xml", shared.encode()), ("xl/worksheets/sheet1.xml", sheet_xml.encode()),
                     ("xl/worksheets/sheet2.xml", f'<worksheet {SNS}><sheetData/></worksheet>'.encode())])


SHEET = (f'<?xml version="1.0"?><worksheet {SNS}><sheetData>'
         '<row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c><c r="C1" t="inlineStr"><is><t>ok</t></is></c>'
         '<c r="D1" t="s"><v>2</v></c></row>'
         '<row r="2"><c r="A2" s="1"><v>45292</v></c><c r="B2"><v>1.5</v></c><c r="C2" t="b"><v>1</v></c>'
         '<c r="D2" t="str"><f>A1&amp;"x"</f><v>dayx</v></c></row>'
         '<row r="3"><c r="A3" s="2"><v>45293</v></c><c r="B3"><f>B2*2</f><v>3</v></c><c r="C3" t="b"><v>0</v></c>'
         '<c r="D3" t="e"><v>#DIV/0!</v></c></row>'
         '<row r="4"><c r="A4" s="1"><v>45294</v></c><c r="B4"><f>B3+1</f></c><c r="C4" t="b"><v>1</v></c></row>'
         '</sheetData><mergeCells count="1"><mergeCell ref="A1:A1"/></mergeCells></worksheet>')


def test_xlsx(b1reg):
    out = b1reg.decode(xlsx(SHEET), name="book.xlsx")
    assert out.detected.format == "xlsx"
    members = {m.path: m for m in out.representation.members}
    assert set(members) == {"Data", "Empty"} and members["Empty"].inner is None
    t = members["Data"].inner
    c = {col.name: col for col in t.columns}
    assert list(c) == ["day", "value", "ok", "rich"] and t.title == "Data"
    assert c["day"].kind == "time" and list(c["day"].values) == [1704067200.0, 1704153600.0, 1704240000.0]
    assert c["value"].kind == "number" and c["value"].values[1] == 3.0 and math.isnan(c["value"].values[2])
    assert c["ok"].kind == "boolean"
    assert c["rich"].values[0] == "dayx" and c["rich"].n_missing == 2
    note = members["Data"].note
    assert "3 formula cell(s) are not evaluated" in note and "1 have no cached value" in note
    assert "1 error cell(s)" in note and "merged" in note
    assert "series" in t.shape().kinds


def test_xlsx_date_systems_and_formats():
    assert sheets.serial_to_epoch(45292, False) == 1704067200.0          # 2024-01-01
    assert sheets.serial_to_epoch(1, False) == -2208988800.0             # 1900-01-01
    assert sheets.serial_to_epoch(0, True) == -2082844800.0              # 1904-01-01
    assert sheets.is_date_format("dd/mm/yyyy") and not sheets.is_date_format('0.00" days"')
    assert not sheets.is_date_format("[Red]0.0") and sheets.is_time_only("hh:mm")


def test_xlsx_hostile_cell_refs(b1reg):
    far = f'<worksheet {SNS}><sheetData><row r="1"><c r="XFD1"><v>1</v></c></row><row r="2000"><c r="A2000"><v>2</v></c></row></sheetData></worksheet>'
    with pytest.raises(T.Refused):
        b1reg.decode(xlsx(far), name="far.xlsx", limits=T.Limits(max_elements=100_000))
    bad = f'<worksheet {SNS}><sheetData><row r="1"><c r="A1" t="s"><v>99</v></c></row></sheetData></worksheet>'
    with pytest.raises(T.Malformed):
        b1reg.decode(xlsx(bad), name="bad.xlsx")


def test_ods(b1reg):
    from test_b1_office import ODF, odf
    content = f"""<?xml version="1.0"?><office:document-content {ODF}><office:body><office:spreadsheet>
<table:table table:name="Prices"><table:table-row><table:table-cell office:value-type="string"><text:p>item</text:p></table:table-cell>
<table:table-cell office:value-type="string"><text:p>eur</text:p></table:table-cell><table:table-cell office:value-type="string"><text:p>on</text:p></table:table-cell>
<table:table-cell office:value-type="string"><text:p>when</text:p></table:table-cell></table:table-row>
<table:table-row><table:table-cell office:value-type="string"><text:p>tea</text:p></table:table-cell>
<table:table-cell office:value-type="float" office:value="2.5" table:formula="of:=1+1.5"><text:p>2,50</text:p></table:table-cell>
<table:table-cell office:value-type="boolean" office:boolean-value="true"><text:p>TRUE</text:p></table:table-cell>
<table:table-cell office:value-type="date" office:date-value="2024-01-02"><text:p>02.01.24</text:p></table:table-cell></table:table-row>
<table:table-row table:number-rows-repeated="1048570"><table:table-cell table:number-columns-repeated="1024"/></table:table-row>
</table:table></office:spreadsheet></office:body></office:document-content>"""
    out = b1reg.decode(odf("application/vnd.oasis.opendocument.spreadsheet", content), name="p.ods")
    assert out.detected.format == "ods"
    m = out.representation.members[0]
    assert m.path == "Prices"
    c = {col.name: col for col in m.inner.columns}
    assert c["eur"].values[0] == 2.5 and c["on"].kind == "boolean" and c["when"].values[0] == 1704153600.0
    assert m.inner.n_rows == 1 and "formulas are not evaluated" in m.note


@pytest.fixture
def db_bytes(tmp_path):
    path = tmp_path / "t.sqlite"
    con = sqlite3.connect(path)
    con.executescript("""
        CREATE TABLE readings (t TEXT, value REAL, tag TEXT, raw BLOB);
        INSERT INTO readings VALUES ('2024-01-01', 1.5, 'a', x'00ff'), ('2024-01-02', 2.5, 'b', NULL);
        CREATE TABLE "we""ird" (n INTEGER);
        INSERT INTO "we""ird" VALUES (7);
        CREATE VIEW v AS SELECT * FROM readings;
        CREATE TRIGGER tr AFTER INSERT ON readings BEGIN DELETE FROM readings; END;
        CREATE INDEX ix ON readings(tag);
    """)
    try:
        con.execute("CREATE VIRTUAL TABLE docs USING fts5(body)")
    except sqlite3.OperationalError:
        pass
    con.commit()
    con.close()
    return path.read_bytes()


def test_sqlite(b1reg, db_bytes):
    original = bytes(db_bytes)
    out = b1reg.decode(db_bytes, name="t.sqlite")
    assert out.detected.format == "sqlite"
    m = {x.path: x for x in out.representation.members}
    t = m["readings"].inner
    c = {col.name: col for col in t.columns}
    assert c["t"].kind == "time" and list(c["value"].values) == [1.5, 2.5] and c["raw"].n_missing == 2
    assert "2 row(s)" in m["readings"].note and "1 blob cell(s) not decoded" in m["readings"].note
    assert list(m['we"ird'].inner.columns[0].values) == [7]
    assert m["v"].inner is None and "never evaluated" in m["v"].note
    assert m["tr"].inner is None and "never evaluated" in m["tr"].note
    if "docs" in m:
        assert m["docs"].inner is None and "virtual" in m["docs"].note
    assert any("page_size=4096" in n for n in out.notes)
    assert db_bytes == original


def test_sqlite_connection_cannot_write_or_attach(db_bytes):
    from neuroscifi.artifacts.safe_b1 import Deadline
    conn = sqlite_db.open_locked(db_bytes, Deadline(10))
    try:
        for sql in ("CREATE TABLE x(a)", "INSERT INTO readings(tag) VALUES ('z')", "ATTACH ':memory:' AS o",
                    "SELECT load_extension('x')", "PRAGMA writable_schema=ON", "SELECT * FROM v"):
            with pytest.raises(sqlite3.DatabaseError):
                conn.execute(sql).fetchall()
    finally:
        conn.close()


def test_sqlite_damaged_and_timeout(b1reg, db_bytes):
    damaged = db_bytes[:100] + b"\xff" * (len(db_bytes) - 100)
    with pytest.raises(T.ArtifactError):
        b1reg.decode(damaged, name="d.sqlite")
    with pytest.raises(T.Malformed):
        b1reg.decode(b"SQLite format 3\x00" + b"\x00" * 10, name="short.sqlite")
    out = b1reg.decode(db_bytes, name="t.sqlite", limits=T.Limits(max_seconds=0.0))
    assert out.partial or all(m.inner is None for m in out.representation.members)


def test_sqlite_progress_handler_interrupts_a_long_statement(tmp_path):
    from neuroscifi.artifacts.safe_b1 import Deadline
    path = tmp_path / "big.sqlite"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE big (n INTEGER)")
    con.executemany("INSERT INTO big VALUES (?)", ((i,) for i in range(50_000)))
    con.commit()
    con.close()
    conn = sqlite_db.open_locked(path.read_bytes(), Deadline(0.0))
    try:
        with pytest.raises(sqlite3.OperationalError, match="interrupted"):
            conn.execute("SELECT count(*) FROM big WHERE n % 7 = 3").fetchone()
    finally:
        conn.close()
