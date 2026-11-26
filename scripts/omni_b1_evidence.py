"""Measure lane B1's adapters (OMNIMIND) on a corpus this script builds itself: per adapter, files tried, decoded,
partial, refused (by rule) and failed, and peak memory (tracemalloc) and wall time on the largest honest input and
the worst hostile input. Writes docs/quality/evidence/omnimind_b1.json (a summary, not a history).

Usage: PYTHONPATH=src python scripts/omni_b1_evidence.py [--out PATH]
The registry is the merged one (lane K's built-ins, then B1's adapters) with K's `detect`; each row names the adapter
that `resolve` actually picked (the manifest example is read by the TOML adapter until detect names manifests).
Each input is decoded twice: once for time (no tracing), once under tracemalloc for the peak of Python and numpy
allocations (C-level buffers of sqlite3 and zlib are not traced: the numbers are lower bounds for those)."""
from __future__ import annotations

import argparse
import gzip
import io
import json
import platform
import sqlite3
import struct
import sys
import tarfile
import tempfile
import time
import tracemalloc
import zlib
from pathlib import Path

from neuroscifi.artifacts import adapters, detect as detect_mod
from neuroscifi.artifacts.adapters import _b1
from neuroscifi.artifacts.types import ArtifactError, Limits, Opaque

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests" / "artifacts"))
from _b1_support import make_zip  # noqa: E402  (the tests' hand-assembled ZIP writer)


def setup() -> None:
    _b1.register_all()           # after lane K's built-ins: the most recently registered adapter wins


# ---------------------------------------------------------------------------------------------------------------
# Corpus: (adapter, label, kind "honest"|"hostile", name, bytes)


def corpus():
    out = []

    def add(adapter, label, kind, name, data):
        out.append((adapter, label, kind, name, data))

    rows = "".join(f"2024-01-{1 + i % 28:02d},{i},{i * 0.5},city{i % 50},{'yes' if i % 2 else 'no'}\n"
                   for i in range(200_000))
    add("b1-delimited", "csv 200k rows x 5", "honest", "big.csv", ("day,n,x,city,ok\n" + rows).encode())
    add("b1-delimited", "csv 2 small", "honest", "s.csv", b"a;b\n1,5;2\n3;4\n")
    add("b1-delimited", "csv field over 128 KiB", "hostile", "f.csv", b"a,b\n" + b"x" * 300_000 + b",1\n")
    add("b1-delimited", "csv 1M one-char cells", "hostile", "w.csv", (b"a," * 500_000 + b"a\n") * 3)
    add("b1-ndjson", "ndjson 50k records", "honest", "r.ndjson",
        "".join(json.dumps({"t": f"2024-02-{1 + i % 28:02d}", "v": i, "s": "x" * 10}) + "\n"
                for i in range(50_000)).encode())
    add("b1-ndjson", "ndjson nesting 50k deep", "hostile", "d.ndjson", (b"[" * 50_000 + b"]" * 50_000 + b"\n") * 2)

    md = "".join(f"## Section {i}\n\nWords of paragraph {i} with a [link](http://x/{i}).\n\n- a\n- b\n\n"
                 for i in range(20_000))
    add("b1-markdown", "markdown 1.5 MB", "honest", "big.md", md.encode())
    html = "<html><body>" + "".join(f"<h2>H{i}</h2><p>Para {i}</p><table><tr><th>k</th><th>v</th></tr>"
                                    f"<tr><td>a</td><td>{i}</td></tr></table>" for i in range(10_000)) + "</body></html>"
    add("b1-html", "html 10k sections with tables", "honest", "big.html", html.encode())
    add("b1-html", "html 1M empty tags", "hostile", "tags.html", b"<html><body>" + b"<i></i>" * 1_000_000)
    add("b1-html", "html 100k nested divs", "hostile", "nest.html", b"<div>" * 100_000 + b"x")
    rtf = b"{\\rtf1\\ansi " + b"".join(b"Paragraph %d text.\\par " % i for i in range(50_000)) + b"}"
    add("b1-rtf", "rtf 50k paragraphs", "honest", "big.rtf", rtf)
    add("b1-rtf", "rtf 100k nested groups", "hostile", "g.rtf", b"{\\rtf1 " + b"{" * 100_000 + b"}" * 100_000 + b"}")

    wns = ('xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"')
    body = "".join(f"<w:p><w:r><w:t>Paragraph {i}</w:t></w:r></w:p>" for i in range(20_000))
    docx = make_zip([("[Content_Types].xml", b"<Types/>"),
                     ("word/document.xml", f"<w:document {wns}><w:body>{body}</w:body></w:document>".encode())])
    add("b1-docx", "docx 20k paragraphs", "honest", "big.docx", docx)
    ents = b'<!ENTITY a "aaaaaaaaaa">' + b"".join(
        b'<!ENTITY ' + bytes([98 + i]) + b' "' + (b"&" + bytes([97 + i]) + b";") * 10 + b'">' for i in range(8))
    laughs = (b'<?xml version="1.0"?><!DOCTYPE l [' + ents + b']><w:document ' + wns.encode() +
              b'><w:body><w:p><w:r><w:t>&i;</w:t></w:r></w:p></w:body></w:document>')
    add("b1-docx", "docx billion laughs", "hostile", "laughs.docx",
        make_zip([("[Content_Types].xml", b"<Types/>"), ("word/document.xml", laughs)]))
    add("b1-docx", "docx 100 MB of zeros in document.xml", "hostile", "zero.docx",
        make_zip([("[Content_Types].xml", b"<Types/>"), ("word/document.xml", b"\x00" * (100 << 20))]))

    sns = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" ' \
          'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
    sheet_rows = "".join(f'<row r="{r}"><c r="A{r}"><v>{r}</v></c><c r="B{r}"><v>{r * 1.5}</v></c>'
                         f'<c r="C{r}" t="inlineStr"><is><t>n{r % 30}</t></is></c></row>' for r in range(1, 30_001))
    wb = (f'<workbook {sns}><sheets><sheet name="S" sheetId="1" r:id="rId1"/></sheets></workbook>').encode()
    rels = (b'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            b'<Relationship Id="rId1" Type="w" Target="worksheets/sheet1.xml"/></Relationships>')

    def xlsx(sheet):
        return make_zip([("[Content_Types].xml", b"<Types/>"), ("xl/workbook.xml", wb),
                         ("xl/_rels/workbook.xml.rels", rels), ("xl/worksheets/sheet1.xml", sheet)])
    add("b1-xlsx", "xlsx 30k rows x 3", "honest", "big.xlsx",
        xlsx(f"<worksheet {sns}><sheetData>{sheet_rows}</sheetData></worksheet>".encode()))
    add("b1-xlsx", "xlsx cell at XFD1048575", "hostile", "far.xlsx",
        xlsx(f'<worksheet {sns}><sheetData><row r="1"><c r="A1"><v>1</v></c></row><row r="1048575">'
             f'<c r="XFD1048575"><v>1</v></c></row></sheetData></worksheet>'.encode()))
    odf_ns = ('xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
              'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" '
              'xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0"')
    ods_rows = "".join(f'<table:table-row><table:table-cell office:value-type="float" office:value="{i}"/>'
                       f'<table:table-cell office:value-type="string"><text:p>r{i}</text:p></table:table-cell>'
                       f'</table:table-row>' for i in range(10_000))

    def ods(rows_xml):
        content = (f'<office:document-content {odf_ns}><office:body><office:spreadsheet><table:table table:name="T">'
                   f'{rows_xml}</table:table></office:spreadsheet></office:body></office:document-content>').encode()
        return make_zip([("mimetype", b"application/vnd.oasis.opendocument.spreadsheet"), ("content.xml", content)],
                        method=0)
    add("b1-ods", "ods 10k rows", "honest", "big.ods", ods(ods_rows))
    add("b1-ods", "ods 1M x 1024 repeated cells", "hostile", "rep.ods",
        ods('<table:table-row table:number-rows-repeated="1000000"><table:table-cell office:value-type="float" '
            'office:value="1" table:number-columns-repeated="1024"/></table:table-row>'))

    pages = []
    for i in range(50):
        pages.append(f"Page {i} text")
    add("b1-pdf", "pdf 50 pages", "honest", "big.pdf", _pdf(pages))
    add("b1-pdf", "pdf 2000 pages, 1 s deadline", "hostile", "many.pdf", _pdf([f"p{i}" for i in range(2000)]),)

    xml = b'<?xml version="1.0"?><r>' + b"".join(b'<i n="%d">v%d</i>' % (i, i) for i in range(200_000)) + b"</r>"
    add("b1-xml", "xml 200k elements", "honest", "big.xml", xml)
    add("b1-xml", "xml 100k deep", "hostile", "deep.xml", b'<?xml version="1.0"?>' + b"<x>" * 100_000 + b"</x>" * 100_000)
    add("b1-xml", "xml external entity", "hostile", "xxe.xml",
        b'<?xml version="1.0"?><!DOCTYPE r [<!ENTITY x SYSTEM "file:///etc/passwd">]><r>&x;</r>')
    add("b1-toml", "toml 20k keys", "honest", "big.toml", "".join(f"k{i} = {i}\n" for i in range(20_000)).encode())
    add("b1-toml", "toml 10k nested arrays", "hostile", "deep.toml", b"a = " + b"[" * 10_000 + b"]" * 10_000 + b"\n")
    add("b1-ini", "ini 2k sections", "honest", "big.ini",
        "".join(f"[s{i}]\nk = v{i}\n" for i in range(2_000)).encode())
    add("b1-plist", "plist 10k keys", "honest", "big.plist",
        b'<?xml version="1.0"?><plist version="1.0"><dict>' +
        b"".join(b"<key>k%d</key><integer>%d</integer>" % (i, i) for i in range(10_000)) + b"</dict></plist>")
    add("b1-yaml", "yaml (no parser)", "honest", "c.yaml", b"a: 1\nb: [1, 2]\n")

    py = "".join(f"def f{i}(x):\n    return f{max(0, i - 1)}(x) + len(x)\n\n" for i in range(5_000))
    add("b1-python", "python 5k functions", "honest", "big.py", py.encode())
    add("b1-python", "python 100k unary minus", "hostile", "deep.py", b"x = " + b"-" * 100_000 + b"1\n")
    add("b1-code-surface", "c 10k functions", "honest", "big.c",
        "".join(f"int f{i}(void)\n{{\n  return {i};\n}}\n" for i in range(10_000)).encode())
    add("b1-manifests", "pyproject", "honest", "pyproject.toml",
        b'[project]\nname = "x"\ndependencies = ["numpy>=2", "zstandard"]\n')
    nb = {"nbformat": 4, "nbformat_minor": 5, "metadata": {},
          "cells": [{"cell_type": "code", "source": f"x = {i}", "metadata": {}, "execution_count": i,
                     "outputs": [{"output_type": "stream", "name": "stdout", "text": f"{i}\n"}]}
                    for i in range(2_000)]}
    add("b1-notebook", "ipynb 2k cells", "honest", "big.ipynb", json.dumps(nb).encode())

    add("b1-zip", "zip 500 csv members", "honest", "many.zip",
        make_zip([(f"d/{i}.csv", b"a,b\n1,2\n3,4\n") for i in range(500)]))
    add("b1-zip", "zip 1 GiB of zeros in one member", "hostile", "bomb.zip", _zip_bomb(1 << 30))
    nested = b"a,b\n1,2\n"
    for i in range(12):
        nested = make_zip([(f"l{i}.zip" if i else "x.csv", nested)])
    add("b1-zip", "zip nested 12 deep", "hostile", "nest.zip", nested)
    add("b1-zip", "zip with 70k entries (60000 declared)", "hostile", "flood.zip", _zip_flood())
    tbuf = io.BytesIO()
    with tarfile.open(fileobj=tbuf, mode="w") as tf:
        for i in range(500):
            info = tarfile.TarInfo(f"d/{i}.csv")
            payload = b"a,b\n1,2\n"
            info.size = len(payload)
            tf.addfile(info, io.BytesIO(payload))
    add("b1-tar", "tar.gz 500 csv members", "honest", "many.tgz", gzip.compress(tbuf.getvalue()))
    zbuf = io.BytesIO()
    with tarfile.open(fileobj=zbuf, mode="w") as tf:
        info = tarfile.TarInfo("z")
        info.size = 200 << 20
        tf.addfile(info, _Zeros(info.size))
    add("b1-tar", "tar.gz with 200 MiB of zeros", "hostile", "bomb.tgz", gzip.compress(zbuf.getvalue(), 9))
    add("b1-7z-rar", "7z header", "honest", "x.7z", b"7z\xbc\xaf\x27\x1c\x00\x04" + b"\x00" * 24)

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "d.sqlite"
        con = sqlite3.connect(path)
        con.execute("CREATE TABLE t (day TEXT, v REAL, s TEXT)")
        con.executemany("INSERT INTO t VALUES (?, ?, ?)",
                        ((f"2024-03-{1 + i % 28:02d}", i * 0.25, f"s{i % 9}") for i in range(100_000)))
        con.execute("CREATE VIEW slow AS SELECT a.v FROM t a, t b, t c")
        con.commit()
        con.close()
        db = path.read_bytes()
    add("b1-sqlite", "sqlite 100k rows + cross-join view", "honest", "d.sqlite", db)
    add("b1-sqlite", "sqlite with damaged pages", "hostile", "bad.sqlite", db[:100] + bytes(range(256)) * 400)
    return out


class _Zeros(io.RawIOBase):
    def __init__(self, n):
        self.n = n

    def readable(self):
        return True

    def readinto(self, b):
        k = min(len(b), self.n)
        b[:k] = bytes(k)
        self.n -= k
        return k


def _pdf(pages):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests" / "artifacts"))
    from test_b1_pdf import build_pdf
    return build_pdf(pages)


def _zip_bomb(size: int) -> bytes:
    co = zlib.compressobj(9, zlib.DEFLATED, -15)
    chunk = bytes(1 << 20)
    comp = b"".join(co.compress(chunk) for _ in range(size >> 20)) + co.flush()
    crc = 0
    for _ in range(size >> 20):
        crc = zlib.crc32(chunk, crc)
    name = b"zeros.txt"
    local = struct.pack("<4s5H3L2H", b"PK\x03\x04", 20, 0, 8, 0, 0, crc, len(comp), size, len(name), 0) + name + comp
    central = struct.pack("<4s6H3L5H2L", b"PK\x01\x02", 20, 20, 0, 8, 0, 0, crc, len(comp), size, len(name), 0, 0, 0,
                          0, 0, 0) + name
    end = struct.pack("<4s4H2LH", b"PK\x05\x06", 0, 0, 1, 1, len(central), len(local), 0)
    return local + central + end


def _zip_flood() -> bytes:
    """One stored empty member, then 70 000 central entries for it while the end record declares 60 000."""
    local = struct.pack("<4s5H3L2H", b"PK\x03\x04", 20, 0, 0, 0, 0, 0, 0, 0, 1, 0) + b"a"
    entry = struct.pack("<4s6H3L5H2L", b"PK\x01\x02", 20, 20, 0, 0, 0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0) + b"a"
    central = entry * 70_000
    end = struct.pack("<4s4H2LH", b"PK\x05\x06", 0, 0, 60_000, 60_000, len(central), len(local), 0)
    return local + central + end


# ---------------------------------------------------------------------------------------------------------------


def outcome(data, name, limits):
    try:
        result = adapters.decode(data, name=name, limits=limits)
    except ArtifactError as error:
        return {"outcome": error.reason, "rule": getattr(error, "rule", None)}
    if isinstance(result.representation, Opaque):
        return {"outcome": "opaque"}
    return {"outcome": "partial" if result.partial else "decoded", "format": result.detected.format}


def measure(item, limits):
    _, label, kind, name, data = item
    picked = adapters.resolve(detect_mod.detect(data[:64 << 10], name=name, size=len(data)))
    adapter = picked.name if picked is not None else "(none)"
    t0 = time.perf_counter()
    first = outcome(data, name, limits)
    seconds = time.perf_counter() - t0
    tracemalloc.start()
    outcome(data, name, limits)
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return dict(first, adapter=adapter, input=label, kind=kind, bytes=len(data), seconds=round(seconds, 3),
                peak_mib=round(peak / (1 << 20), 2))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path("docs/quality/evidence/omnimind_b1.json"))
    args = parser.parse_args()
    setup()
    limits = Limits()
    rows = [measure(item, limits if "deadline" not in item[1] else Limits(max_seconds=1.0)) for item in corpus()]
    per = {}
    for r in rows:
        a = per.setdefault(r["adapter"], {"tried": 0, "decoded": 0, "partial": 0, "refused": 0, "malformed": 0,
                                          "unsupported": 0, "other": 0, "largest_honest": None, "worst_hostile": None})
        a["tried"] += 1
        key = r["outcome"] if r["outcome"] in a else ("refused" if r["outcome"] in ("too_large", "timeout")
                                                     else "other")
        a[key] += 1
        slot = "largest_honest" if r["kind"] == "honest" else "worst_hostile"
        best = a[slot]
        rank = (r["bytes"] if r["kind"] == "honest" else r["peak_mib"] * 1000 + r["seconds"])
        if best is None or rank > best["_rank"]:
            a[slot] = {k: r[k] for k in ("input", "bytes", "outcome", "seconds", "peak_mib")} | {"_rank": rank}
            if r.get("rule"):
                a[slot]["rule"] = r["rule"]
    for a in per.values():
        for slot in ("largest_honest", "worst_hostile"):
            if a[slot]:
                a[slot].pop("_rank")
    report = {
        "lane": "B1", "what": "OMNIMIND lane B1 adapters on a corpus built by scripts/omni_b1_evidence.py",
        "host": {"machine": platform.machine(), "python": platform.python_version(),
                 "sqlite": sqlite3.sqlite_version},
        "limits": "DESIGN §2 defaults (the PDF deadline case uses max_seconds=1)",
        "memory": "tracemalloc peak: Python and numpy allocations; C buffers of sqlite3/zlib are not traced",
        "per_adapter": per,
        "inputs": rows,
    }
    text = json.dumps(report, indent=1, sort_keys=True)
    report["size_bytes_of_this_file"] = len(text.encode()) + 40
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=1, sort_keys=True) + "\n")
    print(json.dumps({k: {x: v[x] for x in ("tried", "decoded", "partial", "refused", "malformed", "unsupported")}
                      for k, v in per.items()}, indent=0))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
