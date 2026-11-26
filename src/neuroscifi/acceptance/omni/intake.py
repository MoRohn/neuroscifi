"""Family `intake`: one modality at a time. Each case of cases.INTAKE names a format and its parameters; the generator
below chooses the values (numpy Generator seeded with the case's seed), assembles the file with enc.py, and states
the questions with their truths from the chosen values. Nothing is read back to make a truth.

Formats: tables (CSV with quoting, delimiters, decimal commas and missing cells; TSV; JSON records; NDJSON; XLSX with
shared strings, numbers, dates and two sheets), documents (Markdown, HTML, DOCX, ODT, EPUB, a hand-built PDF),
images (PNG gray/RGB/RGBA/palette, 2/8/16 bit, interlaced; PGM/PPM binary and ASCII; BMP 24/32 bit; GIF with two
frames; SVG shapes), sound (WAV 8/16/24 bit, mono and stereo, tones and bursts), meshes (OBJ, ASCII and binary STL,
ASCII and binary PLY: cube, tetrahedron, open box), geography (GeoJSON, GPX), trees (JSON, XML), code (a module,
a package in a zip), notebooks (ipynb), arrays (npy in four layouts, npz), archives (zip, tar, tar.gz, a gzipped
CSV).
"""
from __future__ import annotations

import json

import numpy as np

from . import Task, check_split, default_score, enc
from .cases import INTAKE

WORDS = ("amber", "basalt", "cinder", "delta", "ember", "fjord", "garnet", "harbor", "indigo", "juniper", "kelp",
         "lagoon", "meadow", "nickel", "orchid", "pewter", "quartz", "raven", "sable", "thistle", "umber", "violet",
         "willow", "xenon", "yarrow", "zinc", "copper", "dune", "falcon", "glacier", "heron", "iris")


def rng_for(seed: int) -> np.random.Generator:
    return np.random.default_rng(seed)


def word(rng) -> str:
    return WORDS[int(rng.integers(len(WORDS)))]


def sentence(rng, n: int = 8) -> str:
    words = [word(rng) for _ in range(n)]
    return " ".join(words).capitalize() + "."


def phrase(rng, n: int = 5) -> str:
    return " ".join(word(rng) for _ in range(n))


Q = tuple            # (question, expect, tolerance, kind)


def q(question, expect, tolerance=None, kind="measure") -> Q:
    return (question, expect, tolerance, kind)


ONE_OF = {"one_of": True}
REL = {"rel": 1e-9}

# ---- tables ---------------------------------------------------------------------------------------------------------


def table_values(rng, case) -> dict:
    """Columns (text, number with 2 decimals, integer, text that needs quoting) chosen for a case."""
    names = case["columns"]
    n = case["rows"]
    text = [word(rng) + "-" + str(int(rng.integers(10, 99))) for _ in range(n)]
    nums = [float(f"{rng.uniform(-50, 250):.2f}") for _ in range(n)]
    ints = [int(rng.integers(0, 5000)) for _ in range(n)]
    makers = (lambda: f"{word(rng)}, {word(rng)}", lambda: f'said "{word(rng)}"',
              lambda: f"{word(rng)}\n{word(rng)}", lambda: f"{word(rng)} {word(rng)}")
    notes = [makers[i % 4]() if case.get("quote_text") else makers[3]() for i in range(n)]
    missing = sorted(rng.choice(n, size=case.get("missing", 0), replace=False).tolist())
    nums_with_missing = [None if i in missing else v for i, v in enumerate(nums)]
    return {"names": names, "cols": [text, nums_with_missing, ints, notes], "kinds": ["text", "number", "integer",
                                                                                        "text"]}


def _field(value, delim: str, decimal: str) -> str:
    if value is None:
        return ""
    s = f"{value:.2f}".replace(".", decimal) if isinstance(value, float) else str(value)
    if any(ch in s for ch in (delim, '"', "\n", "\r")):
        return '"' + s.replace('"', '""') + '"'
    return s


def csv_bytes(t: dict, *, delim=",", decimal=".", crlf=False, bom=False) -> bytes:
    end = "\r\n" if crlf else "\n"
    lines = [delim.join(t["names"])]
    for row in zip(*t["cols"]):
        lines.append(delim.join(_field(v, delim, decimal) for v in row))
    text = end.join(lines) + end
    return (b"\xef\xbb\xbf" if bom else b"") + text.encode("utf-8")


def table_questions(t: dict, rng) -> list[Q]:
    names, cols = t["names"], t["cols"]
    n = len(cols[0])
    present = [v for v in cols[1] if v is not None]
    r_text = int(rng.integers(n))
    present_rows = [i for i in range(n) if cols[1][i] is not None]
    r_num = present_rows[int(rng.integers(len(present_rows)))]
    return [q("table.n_rows", n), q("table.n_cols", len(names)), q("table.columns", list(names)),
            q(f"table.column_mean:{names[1]}", sum(present) / len(present), REL),
            q(f"table.column_mean:{names[2]}", sum(cols[2]) / n, REL),
            q(f"table.cell:{r_text},3", cols[3][r_text]), q(f"table.cell:{r_num},1", cols[1][r_num], REL),
            q(f"table.cell:{r_text},0", cols[0][r_text]),
            q(f"table.n_missing:{names[1]}", sum(v is None for v in cols[1]))]


def gen_csv(case, rng):
    t = table_values(rng, case)
    delim = {"csv": case.get("delimiter", ","), "tsv": "\t"}[case["fmt"]]
    data = csv_bytes(t, delim=delim, decimal=case.get("decimal", "."), crlf=case.get("crlf", False))
    name = case.get("file", "table." + case["fmt"])
    fmt = ["csv"] if case["fmt"] == "csv" else ["tsv", "csv"]
    return ({name: data}, {name: "text/csv" if case["fmt"] == "csv" else "text/tab-separated-values"},
            table_questions(t, rng) + [q("detect.family", "table"), q("detect.format", fmt, ONE_OF)])


def gen_json_records(case, rng):
    t = table_values(rng, case)
    rows = [dict(zip(t["names"], row)) for row in zip(*t["cols"])]
    if case["fmt"] == "ndjson":
        data = ("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n").encode("utf-8")
        name, declared, fmt = case.get("file", "records.ndjson"), "application/x-ndjson", \
            ["ndjson", "jsonl", "jsonlines", "json-lines"]
    else:
        data = enc.jdump(rows)
        name, declared, fmt = case.get("file", "records.json"), "application/json", ["json"]
    return ({name: data}, {name: declared}, table_questions(t, rng) + [q("detect.format", fmt, ONE_OF)])


def _col(i: int) -> str:
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def xlsx_bytes(sheets: list[tuple[str, list[str], list[list]]]) -> bytes:
    """sheets: (name, header, rows); a row value is str (shared string), float/int (number) or ("date", serial)."""
    strings: list[str] = []

    def sid(s):
        if s not in strings:
            strings.append(s)
        return strings.index(s)

    sheet_xml = []
    for _, header, rows in sheets:
        out = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
               '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>']
        for r, row in enumerate([header] + rows):
            cells = []
            for c, v in enumerate(row):
                ref = f"{_col(c)}{r + 1}"
                if v is None:
                    continue
                if isinstance(v, str):
                    cells.append(f'<c r="{ref}" t="s"><v>{sid(v)}</v></c>')
                elif isinstance(v, tuple):
                    cells.append(f'<c r="{ref}" s="1"><v>{v[1]}</v></c>')
                else:
                    cells.append(f'<c r="{ref}"><v>{v!r}</v></c>')
            out.append(f'<row r="{r + 1}">' + "".join(cells) + "</row>")
        out.append("</sheetData></worksheet>")
        sheet_xml.append("".join(out))
    shared = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><sst xmlns="http://schemas.openxmlformats.org/'
              f'spreadsheetml/2006/main" count="{len(strings)}" uniqueCount="{len(strings)}">'
              + "".join(f"<si><t>{enc.xml_escape(s)}</t></si>" for s in strings) + "</sst>")
    workbook = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><workbook xmlns="http://schemas.openxmlformats'
                '.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/'
                'relationships"><sheets>' + "".join(
                    f'<sheet name="{enc.xml_escape(name)}" sheetId="{i + 1}" r:id="rId{i + 1}"/>'
                    for i, (name, _, _) in enumerate(sheets)) + "</sheets></workbook>")
    rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats'
            '.org/package/2006/relationships">' + "".join(
                f'<Relationship Id="rId{i + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
                f'relationships/worksheet" Target="worksheets/sheet{i + 1}.xml"/>' for i in range(len(sheets)))
            + f'<Relationship Id="rId{len(sheets) + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
              'relationships/sharedStrings" Target="sharedStrings.xml"/>'
            + f'<Relationship Id="rId{len(sheets) + 2}" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
              'relationships/styles" Target="styles.xml"/></Relationships>')
    styles = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><styleSheet xmlns="http://schemas.openxmlformats'
              '.org/spreadsheetml/2006/main"><fonts count="1"><font><sz val="11"/><name val="Calibri"/></font></fonts>'
              '<fills count="1"><fill><patternFill patternType="none"/></fill></fills><borders count="1"><border/>'
              '</borders><cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
              '<cellXfs count="2"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/><xf numFmtId="14" '
              'fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/></cellXfs></styleSheet>')
    types = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/'
             'package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-'
             'package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName='
             '"/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+'
             'xml"/>' + "".join(
                 f'<Override PartName="/xl/worksheets/sheet{i + 1}.xml" ContentType="application/vnd.openxmlformats-'
                 'officedocument.spreadsheetml.worksheet+xml"/>' for i in range(len(sheets)))
             + '<Override PartName="/xl/sharedStrings.xml" ContentType="application/vnd.openxmlformats-officedocument'
               '.spreadsheetml.sharedStrings+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.'
               'openxmlformats-officedocument.spreadsheetml.styles+xml"/></Types>')
    root_rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.'
                 'openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.'
                 'openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
                 '</Relationships>')
    files = {"[Content_Types].xml": types, "_rels/.rels": root_rels, "xl/workbook.xml": workbook,
             "xl/_rels/workbook.xml.rels": rels, "xl/styles.xml": styles, "xl/sharedStrings.xml": shared}
    for i, xml in enumerate(sheet_xml):
        files[f"xl/worksheets/sheet{i + 1}.xml"] = xml
    return enc.zip_files({k: v.encode("utf-8") for k, v in files.items()})


def serial_to_epoch(serial: int) -> float:
    return (serial - 25569) * 86400.0


def workbook_values(rng, case) -> list[tuple[str, list[str], list[list]]]:
    sheets = []
    for k, (sheet, n) in enumerate(zip(case["sheets"], case["rows"])):
        header = [f"{case['columns'][0]}{k}", f"{case['columns'][1]}{k}", f"{case['columns'][2]}{k}"]
        rows = []
        for _ in range(n):
            rows.append([word(rng) + " " + word(rng), float(f"{rng.uniform(0, 900):.3f}"),
                         ("date", int(rng.integers(43000, 46000)))])
        sheets.append((sheet, header, rows))
    return sheets


def gen_xlsx(case, rng):
    sheets = workbook_values(rng, case)
    name = case.get("file", "workbook.xlsx")
    qs = [q("table.sheets", [s for s, _, _ in sheets]), q("detect.format", "xlsx"), q("detect.family", "table")]
    for sheet, header, rows in sheets:
        r = int(rng.integers(len(rows)))
        qs += [q(f"{sheet}::table.n_rows", len(rows)), q(f"{sheet}::table.columns", header),
               q(f"{sheet}::table.cell:{r},0", rows[r][0]), q(f"{sheet}::table.cell:{r},1", rows[r][1], REL),
               q(f"{sheet}::table.cell:{r},2", serial_to_epoch(rows[r][2][1]), {"abs": 1.0}),
               q(f"{sheet}::table.column_mean:{header[1]}", sum(row[1] for row in rows) / len(rows), REL)]
    return ({name: xlsx_bytes(sheets)},
            {name: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}, qs)


# ---- documents ----------------------------------------------------------------------------------------------------


def doc_content(rng, case) -> dict:
    """A title, sections (level, heading, paragraphs), one table (header + rows of [text, number]) and figures."""
    sections = []
    for i in range(case.get("sections", 3)):
        level = 1 if i == 0 else (2 if i % 2 else 3)
        sections.append((level, f"{word(rng).capitalize()} {word(rng)} {i + 1}",
                         [sentence(rng, int(rng.integers(6, 12))) for _ in range(int(rng.integers(1, 3)))]))
    header = [word(rng).capitalize(), word(rng).capitalize() + " value"]
    rows = [[word(rng) + "-" + str(i), float(f"{rng.uniform(0, 100):.2f}")] for i in range(case.get("rows", 4))]
    figures = [f"Figure {i + 1}: {phrase(rng, 4)}" for i in range(case.get("figures", 1))]
    marker = f"The {word(rng)} index of {word(rng)} stays near {int(rng.integers(10, 99))} units."
    return {"title": f"{word(rng).capitalize()} {word(rng)} report", "sections": sections,
            "table": (header, rows), "figures": figures, "marker": marker}


def doc_questions(d: dict, rng, *, title=True, figures=True, table=True) -> list[Q]:
    header, rows = d["table"]
    r = int(rng.integers(len(rows)))
    qs = [q("doc.headings", [h for _, h, _ in d["sections"]]), q(f"doc.contains:{d['marker']}", True),
          q("detect.family", "document")]
    if title:
        qs.append(q("doc.title", d["title"]))
    if table:
        qs += [q(f"doc.table_cell:0,{r},0", rows[r][0]), q(f"doc.table_cell:0,{r},1", rows[r][1], REL)]
    if figures:
        qs.append(q("doc.figures", d["figures"]))
    return qs


def markdown_bytes(d: dict, *, title_heading=False) -> bytes:
    out = []
    for i, (level, heading, paras) in enumerate(d["sections"]):
        out.append("#" * level + " " + heading)
        out.append("")
        out += [p + "\n" for p in paras]
        if i == 0:
            out.append(d["marker"])
            out.append("")
            header, rows = d["table"]
            out.append("| " + " | ".join(header) + " |")
            out.append("|" + "---|" * len(header))
            out += ["| " + " | ".join(f"{v:.2f}" if isinstance(v, float) else v for v in row) + " |" for row in rows]
            out.append("")
            for k, cap in enumerate(d["figures"]):
                out.append(f"![{cap}](figure-{k + 1}.png)")
                out.append("")
    return ("\n".join(out) + "\n").encode("utf-8")


def gen_markdown(case, rng):
    d = doc_content(rng, case)
    name = case.get("file", "report.md")
    return ({name: markdown_bytes(d)}, {name: "text/markdown"},
            doc_questions(d, rng, title=False) + [q("detect.format", ["markdown", "md"], ONE_OF)])


def html_bytes(d: dict) -> bytes:
    e = enc.xml_escape
    out = ["<!DOCTYPE html>", '<html lang="en"><head><meta charset="utf-8">', f"<title>{e(d['title'])}</title>",
           "</head><body><article>"]
    for i, (level, heading, paras) in enumerate(d["sections"]):
        out.append(f"<h{level}>{e(heading)}</h{level}>")
        out += [f"<p>{e(p)}</p>" for p in paras]
        if i == 0:
            out.append(f"<p>{e(d['marker'])}</p>")
            header, rows = d["table"]
            out.append("<table><thead><tr>" + "".join(f"<th>{e(h)}</th>" for h in header) + "</tr></thead><tbody>")
            out += ["<tr>" + "".join(f"<td>{e(f'{v:.2f}' if isinstance(v, float) else v)}</td>" for v in row)
                    + "</tr>" for row in rows]
            out.append("</tbody></table>")
            for k, cap in enumerate(d["figures"]):
                out.append(f'<figure><img src="figure-{k + 1}.png" alt="{e(cap)}"><figcaption>{e(cap)}'
                           "</figcaption></figure>")
    out.append("</article></body></html>")
    return "\n".join(out).encode("utf-8")


def gen_html(case, rng):
    d = doc_content(rng, case)
    name = case.get("file", "article.html")
    return ({name: html_bytes(d)}, {name: "text/html"}, doc_questions(d, rng) + [q("detect.format", "html")])


_W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'


def docx_bytes(d: dict, *, image: bytes | None = None, split_runs=True) -> bytes:
    e = enc.xml_escape

    def para(text, style=None):
        ppr = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ""
        if split_runs and len(text) > 12:
            cut = len(text) // 2
            runs = f'<w:r><w:t xml:space="preserve">{e(text[:cut])}</w:t></w:r><w:r><w:rPr><w:b/></w:rPr>' \
                   f'<w:t xml:space="preserve">{e(text[cut:])}</w:t></w:r>'
        else:
            runs = f'<w:r><w:t xml:space="preserve">{e(text)}</w:t></w:r>'
        return f"<w:p>{ppr}{runs}</w:p>"

    body = []
    for i, (level, heading, paras) in enumerate(d["sections"]):
        body.append(para(heading, f"Heading{level}"))
        body += [para(p) for p in paras]
        if i == 0:
            body.append(para(d["marker"]))
            header, rows = d["table"]
            trs = []
            for row in [header] + rows:
                trs.append("<w:tr>" + "".join(
                    f"<w:tc><w:p><w:r><w:t>{e(f'{v:.2f}' if isinstance(v, float) else v)}</w:t></w:r></w:p></w:tc>"
                    for v in row) + "</w:tr>")
            body.append('<w:tbl><w:tblPr><w:tblStyle w:val="TableGrid"/></w:tblPr>' + "".join(trs) + "</w:tbl>")
            if image is not None:
                body.append(
                    '<w:p><w:r><w:drawing><wp:inline xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/'
                    'wordprocessingDrawing"><wp:extent cx="914400" cy="914400"/><wp:docPr id="1" name="Picture 1" '
                    f'descr="{e(d["figures"][0])}"/><a:graphic xmlns:a="http://schemas.openxmlformats.org/drawingml/'
                    '2006/main"><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture"><pic:pic '
                    'xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture"><pic:nvPicPr><pic:cNvPr '
                    'id="1" name="image1.png"/><pic:cNvPicPr/></pic:nvPicPr><pic:blipFill><a:blip xmlns:r="http://'
                    'schemas.openxmlformats.org/officeDocument/2006/relationships" r:embed="rIdImg1"/></pic:blipFill>'
                    '<pic:spPr/></pic:pic></a:graphicData></a:graphic></wp:inline></w:drawing></w:r></w:p>')
                body.append(para(d["figures"][0], "Caption"))
    document = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document {_W}><w:body>' + "".join(body)
                + "<w:sectPr/></w:body></w:document>")
    styles = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:styles {_W}>' + "".join(
        f'<w:style w:type="paragraph" w:styleId="Heading{k}"><w:name w:val="heading {k}"/><w:pPr><w:outlineLvl '
        f'w:val="{k - 1}"/></w:pPr></w:style>' for k in (1, 2, 3))
        + '<w:style w:type="paragraph" w:styleId="Caption"><w:name w:val="caption"/></w:style></w:styles>')
    core = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><cp:coreProperties xmlns:cp="http://schemas.'
            'openxmlformats.org/package/2006/metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/">'
            f"<dc:title>{e(d['title'])}</dc:title></cp:coreProperties>")
    types = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/'
             'package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-'
             'package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Default Extension='
             '"png" ContentType="image/png"/><Override PartName="/word/document.xml" ContentType="application/vnd.'
             'openxmlformats-officedocument.wordprocessingml.document.main+xml"/><Override PartName="/word/styles.xml"'
             ' ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/><Override '
             'PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
             '</Types>')
    rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats'
            '.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/'
            'officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/><Relationship Id="rId2" '
            'Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target='
            '"docProps/core.xml"/></Relationships>')
    doc_rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.'
                'openxmlformats.org/package/2006/relationships"><Relationship Id="rIdStyles" Type="http://schemas.'
                'openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
                + ('<Relationship Id="rIdImg1" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
                   'relationships/image" Target="media/image1.png"/>' if image is not None else "")
                + "</Relationships>")
    files = {"[Content_Types].xml": types.encode(), "_rels/.rels": rels.encode(), "docProps/core.xml": core.encode(),
             "word/document.xml": document.encode(), "word/styles.xml": styles.encode(),
             "word/_rels/document.xml.rels": doc_rels.encode()}
    if image is not None:
        files["word/media/image1.png"] = image
    return enc.zip_files(files)


def gen_docx(case, rng):
    d = doc_content(rng, dict(case, figures=0))
    name = case.get("file", "report.docx")
    return ({name: docx_bytes(d)},
            {name: "application/vnd.openxmlformats-officedocument.wordprocessingml.document"},
            doc_questions(d, rng, figures=False) + [q("detect.format", "docx")])


def odt_bytes(d: dict) -> bytes:
    e = enc.xml_escape
    ns = ('xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" xmlns:text="urn:oasis:names:tc:opendocument'
          ':xmlns:text:1.0" xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0"')
    body = []
    for i, (level, heading, paras) in enumerate(d["sections"]):
        body.append(f'<text:h text:outline-level="{level}">{e(heading)}</text:h>')
        body += [f"<text:p>{e(p)}</text:p>" for p in paras]
        if i == 0:
            body.append(f"<text:p>{e(d['marker'])}</text:p>")
            header, rows = d["table"]
            trs = ["<table:table-row>" + "".join(
                f'<table:table-cell office:value-type="string"><text:p>{e(h)}</text:p></table:table-cell>'
                for h in header) + "</table:table-row>"]
            for row in rows:
                trs.append("<table:table-row>" + f'<table:table-cell office:value-type="string"><text:p>{e(row[0])}'
                           '</text:p></table:table-cell>' + f'<table:table-cell office:value-type="float" '
                           f'office:value="{row[1]!r}"><text:p>{row[1]:.2f}</text:p></table:table-cell>'
                           "</table:table-row>")
            body.append('<table:table table:name="Table1"><table:table-column table:number-columns-repeated="2"/>'
                        + "".join(trs) + "</table:table>")
    content = (f'<?xml version="1.0" encoding="UTF-8"?><office:document-content {ns} office:version="1.2">'
               "<office:body><office:text>" + "".join(body) + "</office:text></office:body></office:document-content>")
    meta = ('<?xml version="1.0" encoding="UTF-8"?><office:document-meta xmlns:office="urn:oasis:names:tc:opendocument:'
            'xmlns:office:1.0" xmlns:dc="http://purl.org/dc/elements/1.1/" office:version="1.2"><office:meta>'
            f"<dc:title>{e(d['title'])}</dc:title></office:meta></office:document-meta>")
    manifest = ('<?xml version="1.0" encoding="UTF-8"?><manifest:manifest xmlns:manifest="urn:oasis:names:tc:'
                'opendocument:xmlns:manifest:1.0" manifest:version="1.2"><manifest:file-entry manifest:full-path="/" '
                'manifest:media-type="application/vnd.oasis.opendocument.text"/><manifest:file-entry manifest:full-'
                'path="content.xml" manifest:media-type="text/xml"/><manifest:file-entry manifest:full-path="meta.xml" '
                'manifest:media-type="text/xml"/></manifest:manifest>')
    return enc.zip_files({"mimetype": b"application/vnd.oasis.opendocument.text", "content.xml": content.encode(),
                          "meta.xml": meta.encode(), "META-INF/manifest.xml": manifest.encode()},
                         first_stored="mimetype")


def gen_odt(case, rng):
    d = doc_content(rng, dict(case, figures=0))
    name = case.get("file", "note.odt")
    return ({name: odt_bytes(d)}, {name: "application/vnd.oasis.opendocument.text"},
            doc_questions(d, rng, figures=False) + [q("detect.format", "odt")])


def epub_bytes(title: str, chapters: list[list[tuple[int, str, list[str]]]]) -> bytes:
    e = enc.xml_escape
    files = {"mimetype": b"application/epub+zip",
             "META-INF/container.xml": (
                 '<?xml version="1.0"?><container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:'
                 'container"><rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-'
                 'package+xml"/></rootfiles></container>').encode()}
    items, spine = [], []
    for k, sections in enumerate(chapters):
        body = "".join(f"<h{lv}>{e(h)}</h{lv}>" + "".join(f"<p>{e(p)}</p>" for p in ps) for lv, h, ps in sections)
        files[f"OEBPS/chap{k + 1}.xhtml"] = (
            '<?xml version="1.0" encoding="UTF-8"?><html xmlns="http://www.w3.org/1999/xhtml"><head><title>'
            f"{e(title)} {k + 1}</title></head><body>{body}</body></html>").encode()
        items.append(f'<item id="c{k + 1}" href="chap{k + 1}.xhtml" media-type="application/xhtml+xml"/>')
        spine.append(f'<itemref idref="c{k + 1}"/>')
    files["OEBPS/content.opf"] = (
        '<?xml version="1.0" encoding="UTF-8"?><package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-'
        'identifier="id"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:identifier id="id">urn:bench:'
        f'{e(title)}</dc:identifier><dc:title>{e(title)}</dc:title><dc:language>en</dc:language></metadata>'
        f'<manifest>{"".join(items)}</manifest><spine>{"".join(spine)}</spine></package>').encode()
    return enc.zip_files(files, first_stored="mimetype")


def gen_epub(case, rng):
    title = f"{word(rng).capitalize()} {word(rng)} stories"
    chapters = []
    for k in range(case["chapters"]):
        chapters.append([(1, f"Chapter {k + 1}: {word(rng)}", [sentence(rng) for _ in range(2)]),
                         (2, f"{word(rng).capitalize()} {word(rng)}", [sentence(rng)])])
    marker = chapters[-1][-1][2][0]
    name = case.get("file", "book.epub")
    return ({name: epub_bytes(title, chapters)}, {name: "application/epub+zip"},
            [q("doc.title", title), q("doc.headings", [h for ch in chapters for _, h, _ in ch]),
             q(f"doc.contains:{marker}", True), q("detect.format", "epub"), q("detect.family", "document")])


def _pdf_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def pdf_bytes(title: str, pages: list[list[str]]) -> bytes:
    """A PDF 1.4 written object by object: Helvetica, one uncompressed content stream per page, an Info title, and
    a cross-reference table with exact offsets."""
    objs: list[bytes] = []
    n_pages = len(pages)
    page_ids = [4 + 2 * i for i in range(n_pages)]
    objs.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objs.append(("<< /Type /Pages /Kids [" + " ".join(f"{p} 0 R" for p in page_ids)
                 + f"] /Count {n_pages} >>").encode())
    objs.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
    for i, lines in enumerate(pages):
        ops = ["BT", "/F1 12 Tf", "72 720 Td", "16 TL"]
        for line in lines:
            ops.append(f"({_pdf_escape(line)}) Tj T*")
        ops.append("ET")
        stream = ("\n".join(ops) + "\n").encode("latin-1")
        objs.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 3 0 R >> >> "
                    f"/Contents {page_ids[i] + 1} 0 R >>".encode())
        objs.append(f"<< /Length {len(stream)} >>\nstream\n".encode() + stream + b"endstream")
    info_id = len(objs) + 1
    objs.append(f"<< /Title ({_pdf_escape(title)}) /Producer (benchmark) >>".encode("latin-1"))
    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for k, body in enumerate(objs):
        offsets.append(len(out))
        out += f"{k + 1} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R /Info {info_id} 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


def gen_pdf(case, rng):
    title = f"{word(rng).capitalize()} {word(rng)} memo"
    pages = [[f"{phrase(rng, 6)} {int(rng.integers(100, 999))}" for _ in range(int(rng.integers(3, 6)))]
             for _ in range(case["pages"])]
    name = case.get("file", "memo.pdf")
    last = pages[-1][int(rng.integers(len(pages[-1])))]
    return ({name: pdf_bytes(title, pages)}, {name: "application/pdf"},
            [q("doc.n_pages", len(pages)), q(f"doc.contains:{pages[0][0]}", True), q(f"doc.contains:{last}", True),
             q("doc.title", title), q("detect.format", "pdf"), q("detect.family", "document")])


# ---- images -------------------------------------------------------------------------------------------------------


def pixels(rng, h: int, w: int, channels: int, top: int) -> np.ndarray:
    """A gradient plus noise, clipped to [0, top]: structure for filters to bite on, values that are not trivial."""
    yy, xx = np.mgrid[0:h, 0:w]
    out = []
    for c in range(channels):
        base = (xx * (c + 1) * 7 + yy * (3 + c) * 5) % (top + 1)
        noise = rng.integers(0, max(2, top // 8), size=(h, w))
        out.append(np.clip(base + noise, 0, top))
    arr = np.stack(out, axis=2).astype(np.uint16 if top > 255 else np.uint8)
    return arr[:, :, 0] if channels == 1 else arr


def luminance(arr: np.ndarray, top: int) -> float:
    a = arr.astype(np.float64)
    if a.ndim == 2:
        return float(a.mean() / top)
    return float((0.2126 * a[:, :, 0] + 0.7152 * a[:, :, 1] + 0.0722 * a[:, :, 2]).mean() / top)


def image_questions(arr: np.ndarray, top: int, rng, fmt, *, pixel_of=None) -> list[Q]:
    h, w = arr.shape[:2]
    pts = [(w - 1, h - 1), (int(rng.integers(w)), int(rng.integers(h)))]
    qs = [q("image.size", [w, h]), q("image.mean_gray", luminance(arr, top), {"abs": 1e-6}),
          q("detect.family", "image"), q("detect.format", fmt, ONE_OF if isinstance(fmt, list) else None)]
    for x, y in pts:
        value = pixel_of(x, y) if pixel_of else [int(v) for v in np.atleast_1d(arr[y, x])]
        qs.append(q(f"image.pixel:{x},{y}", value))
    return qs


def gen_png(case, rng):
    w, h = case["size"]
    bits, mode = case["bits"], case["mode"]
    name = case.get("file", "image.png")
    if mode == "palette":
        k = case.get("colors", 5)
        palette = [(int(rng.integers(256)), int(rng.integers(256)), int(rng.integers(256))) for _ in range(k)]
        idx = rng.integers(0, k, size=(h, w)).astype(np.uint8)
        alpha = [int(rng.integers(256)) for _ in range(k)] if case.get("trns") else None
        data = enc.png(idx, mode="palette", bits=8, palette=palette, interlace=case.get("interlace", False),
                       transparency=bytes(alpha) if alpha else None)
        expanded = np.array([[palette[i] + ((alpha[i],) if alpha else ()) for i in row] for row in idx.tolist()],
                            dtype=np.uint8)
        qs = image_questions(expanded, 255, rng, "png")
        return {name: data}, {name: "image/png"}, qs
    channels = {"gray": 1, "rgb": 3, "rgba": 4}[mode]
    top = (1 << bits) - 1
    arr = pixels(rng, h, w, channels, top)
    data = enc.png(arr, mode=mode, bits=bits, interlace=case.get("interlace", False))
    return {name: data}, {name: "image/png"}, image_questions(arr, top, rng, "png")


def gen_pnm(case, rng):
    w, h = case["size"]
    color = case["fmt"] == "ppm"
    top = case.get("maxval", 255)
    arr = pixels(rng, h, w, 3 if color else 1, top)
    name = case.get("file", "image." + case["fmt"])
    data = enc.pnm(arr, maxval=top, ascii_form=case.get("ascii", False), comment=case.get("comment"))
    fmt = ["ppm", "pnm"] if color else ["pgm", "pnm"]
    return ({name: data}, {name: "image/x-portable-pixmap" if color else "image/x-portable-graymap"},
            image_questions(arr, top, rng, fmt))


def gen_bmp(case, rng):
    w, h = case["size"]
    bits = case.get("bits", 24)
    arr = pixels(rng, h, w, 3 if bits == 24 else 4, 255)
    name = case.get("file", "image.bmp")
    data = enc.bmp(arr, top_down=case.get("top_down", False), bits=bits)
    qs = image_questions(arr[:, :, :3], 255, rng, "bmp")
    if bits == 32:                       # BI_RGB 32-bit: the fourth byte is reserved; either reading is correct
        widened = []
        for question, expect, tolerance, kind in qs:
            if question.startswith("image.pixel:"):
                x, y = (int(v) for v in question.split(":")[1].split(","))
                expect, tolerance = [expect + [int(arr[y, x, 3])], expect], ONE_OF
            widened.append((question, expect, tolerance, kind))
        qs = widened
    return {name: data}, {name: "image/bmp"}, qs


def gen_gif(case, rng):
    w, h = case["size"]
    k = case.get("colors", 4)
    palette = [(int(rng.integers(256)), int(rng.integers(256)), int(rng.integers(256))) for _ in range(k)]
    frames = [rng.integers(0, k, size=(h, w)).astype(np.uint8) for _ in range(case.get("frames", 2))]
    delays = [int(v) for v in case["delays_cs"]]
    name = case.get("file", "anim.gif")
    data = enc.gif(frames, palette, delays)
    x, y = int(rng.integers(w)), int(rng.integers(h))
    first = np.array([[palette[i] for i in row] for row in frames[0].tolist()], dtype=np.uint8)
    return ({name: data}, {name: "image/gif"},
            [q("image.size", [w, h]), q("image.n_frames", len(frames)),
             q("image.frame_delays", [d / 100 for d in delays], {"abs": 1e-9}),
             q(f"image.pixel:{x},{y}", list(palette[int(frames[0][y, x])])),
             q("image.mean_gray", luminance(first, 255), {"abs": 1e-6}), q("detect.format", "gif"),
             q("detect.family", "image")])


def svg_drawing(rng, case) -> tuple[bytes, dict]:
    w, h = case["size"]
    shapes, texts = [], []
    n_circle, n_rect = case["circles"], case["rects"]
    for _ in range(n_circle):
        shapes.append(f'<circle cx="{int(rng.integers(10, w - 10))}" cy="{int(rng.integers(10, h - 10))}" '
                      f'r="{int(rng.integers(2, 9))}" fill="#555"/>')
    for _ in range(n_rect):
        shapes.append(f'<rect x="{int(rng.integers(0, w - 20))}" y="{int(rng.integers(0, h - 20))}" width="'
                      f'{int(rng.integers(4, 18))}" height="{int(rng.integers(4, 18))}" fill="none" stroke="#222"/>')
    pts = " ".join(f"{int(rng.integers(w))},{int(rng.integers(h))}" for _ in range(4))
    shapes.append(f'<polyline points="{pts}" fill="none" stroke="#000"/>')
    for _ in range(case.get("texts", 2)):
        t = phrase(rng, 2)
        texts.append(t)
        shapes.append(f'<text x="{int(rng.integers(w // 2))}" y="{int(rng.integers(12, h))}">{t}</text>')
    data = (f'<?xml version="1.0" encoding="UTF-8"?>\n<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}"'
            f' viewBox="0 0 {w} {h}">\n' + "\n".join(shapes) + "\n</svg>\n").encode()
    return data, {"circles": n_circle, "rects": n_rect, "texts": texts, "size": [float(w), float(h)]}


def gen_svg(case, rng):
    data, t = svg_drawing(rng, case)
    name = case.get("file", "drawing.svg")
    return ({name: data}, {name: "image/svg+xml"},
            [q("drawing.count:circle", t["circles"]), q("drawing.count:rect", t["rects"]),
             q("drawing.texts", t["texts"]), q("drawing.size", t["size"], {"abs": 1e-9}),
             q("detect.format", "svg"), q("detect.family", "drawing")])


# ---- sound --------------------------------------------------------------------------------------------------------


def tone_bursts(rate: int, freq: float, bursts: int, burst_s: float, gap_s: float, lead_s: float,
                amplitude: float = 0.5) -> np.ndarray:
    n_burst, n_gap, n_lead = int(rate * burst_s), int(rate * gap_s), int(rate * lead_s)
    t = np.arange(n_burst) / rate
    ramp = np.minimum(1.0, np.minimum(np.arange(n_burst), np.arange(n_burst)[::-1]) / max(1, int(rate * 0.004)))
    burst = amplitude * np.sin(2 * np.pi * freq * t) * ramp
    parts = [np.zeros(n_lead)]
    for i in range(bursts):
        parts.append(burst)
        parts.append(np.zeros(n_gap))
    return np.concatenate(parts)


def gen_wav(case, rng):
    rate, bits, channels = case["rate"], case["bits"], case["channels"]
    freq = float(case["freq"])
    mono = tone_bursts(rate, freq, case["bursts"], case["burst_s"], case["gap_s"], case.get("lead_s", 0.1))
    samples = mono if channels == 1 else np.stack([mono * (0.4 + 0.6 * c / max(1, channels - 1))
                                                   for c in range(channels)], axis=1)
    name = case.get("file", "sound.wav")
    data = enc.wav(samples, rate, bits=bits, extensible=case.get("extensible", False))
    n = samples.shape[0]
    return ({name: data}, {name: "audio/wav"},
            [q("audio.duration_s", n / rate, {"abs": 1e-6}), q("audio.rate_hz", rate), q("audio.channels", channels),
             q("audio.dominant_hz", freq, {"rel": 0.01}), q("audio.n_onsets", case["bursts"]),
             q("detect.format", "wav"), q("detect.family", "audio")])


# ---- meshes -------------------------------------------------------------------------------------------------------

CUBE_QUADS = ((0, 2, 3, 1), (4, 5, 7, 6), (0, 1, 5, 4), (2, 6, 7, 3), (0, 4, 6, 2), (1, 3, 7, 5))  # outward winding
TETRA_FACES = ((0, 2, 1), (0, 1, 3), (0, 3, 2), (1, 2, 3))                                  # outward winding


def cube(side: float, origin) -> tuple[list, list]:
    verts = [[origin[0] + side * (i & 1), origin[1] + side * ((i >> 1) & 1), origin[2] + side * ((i >> 2) & 1)]
             for i in range(8)]
    return verts, [list(f) for f in CUBE_QUADS]


def triangulate(faces) -> list[list[int]]:
    return [[f[0], f[k], f[k + 1]] for f in faces for k in range(1, len(f) - 1)]


def gen_mesh(case, rng):
    shape, fmt = case["shape"], case["fmt"]
    o = case["origin"]
    if shape in ("cube", "open_box"):
        s = case["side"]
        verts, faces = cube(s, o)
        if shape == "open_box":
            faces = [f for f in faces if f != [4, 5, 7, 6]]           # no top (z = max)
        volume, area = s ** 3, (6 if shape == "cube" else 5) * s * s
        bounds = [list(map(float, o)), [o[0] + s, o[1] + s, o[2] + s]]
    else:
        a, b, c = case["legs"]
        verts = [list(o), [o[0] + a, o[1], o[2]], [o[0], o[1] + b, o[2]], [o[0], o[1], o[2] + c]]
        faces = [list(f) for f in TETRA_FACES]
        volume = a * b * c / 6
        area = (a * b + a * c + b * c) / 2 + 0.5 * float(np.sqrt((a * b) ** 2 + (a * c) ** 2 + (b * c) ** 2))
        bounds = [list(map(float, o)), [o[0] + a, o[1] + b, o[2] + c]]
    tris = triangulate(faces)
    name = case.get("file", "shape." + {"stl_ascii": "stl", "stl_binary": "stl", "ply_binary": "ply"}.get(fmt, fmt))
    if fmt == "obj":
        data = enc.obj(verts, faces if case.get("quads", True) else tris)
    elif fmt == "stl_ascii":
        data = enc.stl_ascii(np.array(verts, dtype=float), tris, name=case.get("solid", "shape"))
    elif fmt == "stl_binary":
        data = enc.stl_binary(np.array(verts, dtype=float), tris)
    else:
        data = enc.ply(verts, faces if case.get("quads", True) else tris, binary=fmt == "ply_binary")
    closed = shape != "open_box"
    qs = [q("mesh.n_faces", len(tris)), q("mesh.watertight", closed),
          q("mesh.bounds", bounds, {"abs": 1e-6}), q("mesh.area", area, {"rel": 1e-6}),
          q("detect.format", {"obj": "obj", "stl_ascii": "stl", "stl_binary": "stl"}.get(fmt, "ply")),
          q("detect.family", "mesh")]
    if closed:
        qs.append(q("mesh.volume", volume, {"rel": 1e-6}))
    return {name: data}, {name: "model/obj" if fmt == "obj" else "model/stl" if "stl" in fmt else "model/ply"}, qs


# ---- geography ----------------------------------------------------------------------------------------------------


def lonlat(rng, center, spread) -> list[float]:
    return [round(center[0] + float(rng.uniform(-spread, spread)), 5),
            round(center[1] + float(rng.uniform(-spread, spread)), 5)]


def bbox(points) -> list[float]:
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    return [min(xs), min(ys), max(xs), max(ys)]


def gen_geojson(case, rng):
    c, s = case["center"], case["spread"]
    feats, allp = [], []
    for i in range(case["points"]):
        p = lonlat(rng, c, s)
        allp.append(p)
        feats.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": p},
                      "properties": {"name": f"{word(rng)}-{i}"}})
    line = [lonlat(rng, c, s) for _ in range(case["line"])]
    allp += line
    feats.append({"type": "Feature", "geometry": {"type": "LineString", "coordinates": line}, "properties": {}})
    ring = [lonlat(rng, c, s) for _ in range(4)]
    ring.append(ring[0])
    allp += ring
    feats.append({"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [ring]}, "properties": {}})
    name = case.get("file", "places.geojson")
    return ({name: enc.jdump({"type": "FeatureCollection", "features": feats})}, {name: "application/geo+json"},
            [q("geo.bbox", bbox(allp), {"abs": 1e-9}), q("geo.n_features", len(feats)),
             q("detect.format", "geojson"), q("detect.family", "geo")])


def gen_gpx(case, rng):
    c, s = case["center"], case["spread"]
    wpts = [lonlat(rng, c, s) for _ in range(case["waypoints"])]
    segs = [[lonlat(rng, c, s) for _ in range(n)] for n in case["segments"]]
    parts = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<gpx version="1.1" creator="benchmark" xmlns="http://www.topografix.com/GPX/1/1">']
    for i, (lon, lat) in enumerate(wpts):
        parts.append(f'<wpt lat="{lat}" lon="{lon}"><name>{word(rng)}-{i}</name></wpt>')
    parts.append(f"<trk><name>{word(rng)}</name>")
    for seg in segs:
        parts.append("<trkseg>" + "".join(f'<trkpt lat="{lat}" lon="{lon}"><ele>{int(rng.integers(0, 900))}</ele>'
                                          "</trkpt>" for lon, lat in seg) + "</trkseg>")
    parts.append("</trk></gpx>")
    allp = wpts + [p for seg in segs for p in seg]
    name = case.get("file", "route.gpx")
    return ({name: "\n".join(parts).encode()}, {name: "application/gpx+xml"},
            [q("geo.bbox", bbox(allp), {"abs": 1e-9}), q("geo.n_points", len(allp)), q("detect.format", "gpx"),
             q("detect.family", "geo")])


# ---- trees, code, notebooks, arrays ----------------------------------------------------------------------------------


def gen_json_tree(case, rng):
    channels = [{"id": f"{word(rng)}-{i}", "gain": float(f"{rng.uniform(0.1, 9):.3f}")} for i in range(case["items"])]
    readings = [int(rng.integers(-500, 500)) for _ in range(case["readings"])]
    doc = {"instrument": {"name": phrase(rng, 2), "channels": channels, "calibrated": bool(rng.integers(2)),
                          "offset": None}, "readings": readings}
    k, r = int(rng.integers(len(channels))), int(rng.integers(len(readings)))
    name = case.get("file", "instrument.json")
    return ({name: enc.jdump(doc)}, {name: "application/json"},
            [q("tree.value:instrument/name", doc["instrument"]["name"]),
             q(f"tree.value:instrument/channels/{k}/gain", channels[k]["gain"], REL),
             q(f"tree.value:instrument/channels/{k}/id", channels[k]["id"]),
             q(f"tree.value:readings/{r}", readings[r]),
             q("tree.value:instrument/calibrated", doc["instrument"]["calibrated"]),
             q("tree.keys:instrument", ["name", "channels", "calibrated", "offset"]),
             q("detect.format", "json"), q("detect.family", "tree")])


def gen_xml_tree(case, rng):
    items = [{"id": f"{word(rng)[:3]}{i}", "name": phrase(rng, 2), "qty": int(rng.integers(1, 99))}
             for i in range(case["items"])]
    version = int(rng.integers(1, 9))
    body = "".join(f'<item id="{it["id"]}"><name>{it["name"]}</name><qty>{it["qty"]}</qty></item>' for it in items)
    data = f'<?xml version="1.0" encoding="UTF-8"?>\n<catalog version="{version}">{body}</catalog>\n'.encode()
    k = int(rng.integers(len(items)))
    name = case.get("file", "catalog.xml")
    return ({name: data}, {name: "application/xml"},
            [q(f"tree.value:item[{k}]/name", items[k]["name"]), q(f"tree.value:item[{k}]/@id", items[k]["id"]),
             q(f"tree.value:item[{k}]/qty", items[k]["qty"]), q("tree.value:@version", version),
             q("detect.format", "xml"), q("detect.family", "tree")])


def python_module(spec: dict) -> str:
    """Source for {"doc", "imports": [...], "functions": {name: [callees]}, "class": (name, {method: [callees]})}.
    A function without callees returns arithmetic on its argument (using math when the module imports it)."""
    lines = [f'"""{spec.get("doc", "A module.")}"""']
    for imp in spec.get("imports", ()):
        lines.append(f"import {imp}")
    for module, names in spec.get("from_imports", {}).items():
        lines.append(f"from {module} import {', '.join(names)}")
    lines.append("")

    def body(callees, indent):
        if not callees:
            if "math" in spec.get("imports", ()):
                return f"{indent}return math.sqrt(abs(x)) + {len(indent)}"
            return f"{indent}return x * {len(indent) + 1}"
        return f"{indent}return " + " + ".join(f"{c}(x)" for c in callees)

    for fname, callees in spec.get("functions", {}).items():
        lines += ["", f"def {fname}(x):", body(callees, "    "), ""]
    if spec.get("class"):
        cname, methods = spec["class"]
        lines += ["", f"class {cname}:"]
        for mname, callees in methods.items():
            lines += [f"    def {mname}(self, x):", body(callees, "        "), ""]
    return "\n".join(lines).rstrip() + "\n"


def code_truth(specs: list[dict]) -> tuple[list[str], dict, list[str]]:
    functions, calls, imports = [], {}, set()
    for spec in specs:
        for f, callees in spec.get("functions", {}).items():
            functions.append(f)
            calls[f] = sorted(set(callees))
        if spec.get("class"):
            for m, callees in spec["class"][1].items():
                functions.append(m)
                calls[m] = sorted(set(callees))
        imports |= {i.rsplit(".", 1)[-1] for i in spec.get("imports", ())}
        imports |= {m.rsplit(".", 1)[-1] for m in spec.get("from_imports", {})}
    return sorted(set(functions)), calls, sorted(imports)


def gen_code(case, rng):
    specs = case["modules"]
    functions, calls, imports = code_truth([m for m in specs])
    picks = [f for f in sorted(calls) if calls[f]][:2] + [f for f in sorted(calls) if not calls[f]][:1]
    qs = [q("code.functions", functions), q("code.imports", imports)] + [q(f"code.calls:{f}", calls[f])
                                                                         for f in picks]
    if case["fmt"] == "py":
        name = case.get("file", specs[0]["path"])
        return ({name: python_module(specs[0]).encode()}, {name: "text/x-python"},
                qs + [q("detect.format", ["python", "py"], ONE_OF), q("detect.family", "code")])
    name = case.get("file", "package.zip")
    data = enc.zip_files({m["path"]: python_module(m).encode() for m in specs})
    return {name: data}, {name: "application/zip"}, qs + [q("detect.format", "zip"), q("detect.family", "archive")]


def gen_ipynb(case, rng):
    cells, code_sources, headings = [], [], []
    for i in range(case["cells"]):
        if i % 2 == 0:
            heading = f"{word(rng).capitalize()} {word(rng)}"
            headings.append(heading)
            cells.append({"cell_type": "markdown", "metadata": {},
                          "source": ["#" * (1 if i == 0 else 2) + " " + heading + "\n", "\n", sentence(rng)]})
        else:
            a, b = int(rng.integers(2, 50)), int(rng.integers(2, 50))
            src = [f"{word(rng)} = {a}\n", f"print({a} * {b})"]
            code_sources.append("".join(src))
            cells.append({"cell_type": "code", "execution_count": len(code_sources), "metadata": {},
                          "outputs": [{"name": "stdout", "output_type": "stream", "text": [f"{a * b}\n"]}],
                          "source": src})
    nb = {"cells": cells, "metadata": {"kernelspec": {"display_name": "Python 3", "language": "python",
                                                      "name": "python3"}}, "nbformat": 4, "nbformat_minor": 5}
    k = int(rng.integers(len(code_sources)))
    name = case.get("file", "analysis.ipynb")
    return ({name: enc.jdump(nb)}, {name: "application/x-ipynb+json"},
            [q("notebook.code_cells", len(code_sources)), q(f"notebook.code:{k}", code_sources[k]),
             q("doc.headings", headings), q("detect.format", ["ipynb", "notebook", "jupyter"], ONE_OF),
             q("detect.family", "notebook")])


def array_values(rng, dtype: str, shape) -> np.ndarray:
    if dtype.endswith(("f8", "f4")):
        return np.round(rng.normal(0, 40, size=shape), 3).astype(dtype)
    if dtype.endswith("i4"):
        return rng.integers(-10000, 10000, size=shape).astype(dtype)
    return rng.integers(0, 256, size=shape).astype(dtype)


def array_questions(a: np.ndarray, rng, prefix="") -> list[Q]:
    idx = tuple(int(rng.integers(s)) for s in a.shape)
    return [q(prefix + "array.shape", list(a.shape)), q(prefix + "array.dtype", a.dtype.name),
            q(prefix + "array.sum", float(np.sum(a.astype(np.float64))), {"rel": 1e-9}),
            q(prefix + f"array.value:{','.join(map(str, idx))}", float(a[idx]), {"rel": 1e-9})]


def gen_npy(case, rng):
    a = array_values(rng, case["dtype"], tuple(case["shape"]))
    name = case.get("file", "array.npy")
    return ({name: enc.npy(a, fortran=case.get("fortran", False))}, {name: "application/octet-stream"},
            array_questions(a, rng) + [q("detect.format", "npy"), q("detect.family", "array")])


def gen_npz(case, rng):
    arrays = {k: array_values(rng, dt, tuple(shape)) for k, (dt, shape) in case["arrays"].items()}
    members = [{"name": f"{k}.npy", "data": enc.npy(a), "method": "store" if i % 2 else "deflate"}
               for i, (k, a) in enumerate(arrays.items())]
    name = case.get("file", "arrays.npz")
    qs = [q("detect.format", "npz"), q("detect.family", "array")]
    for k, a in arrays.items():
        qs += array_questions(a, rng, prefix=f"{k}::")[:3]
    return {name: enc.zip_archive(members)}, {name: "application/octet-stream"}, qs


# ---- archives -----------------------------------------------------------------------------------------------------


def archive_members(rng, case) -> tuple[dict[str, bytes], list[Q]]:
    t = table_values(rng, {"columns": case["columns"], "rows": case["rows"]})
    owner = phrase(rng, 2)
    files = {"readme.txt": (sentence(rng) + "\n").encode(),
             f"{case['folder']}/values.csv": csv_bytes(t),
             "meta/info.json": enc.jdump({"owner": owner, "count": case["rows"]})}
    qs = [q("bundle.members", sorted(files)), q("bundle.n_members", len(files)),
          q(f"{case['folder']}/values.csv::table.n_rows", case["rows"]),
          q(f"{case['folder']}/values.csv::table.column_mean:{t['names'][2]}", sum(t["cols"][2]) / case["rows"], REL),
          q("meta/info.json::tree.value:owner", owner)]
    return files, qs


def gen_archive(case, rng):
    files, qs = archive_members(rng, case)
    kind = case["fmt"]
    if kind == "zip":
        data, name, declared, fmt = enc.zip_files(files), "bundle.zip", "application/zip", ["zip"]
    elif kind == "tar":
        data, name, declared, fmt = enc.tar_archive(files), "bundle.tar", "application/x-tar", ["tar"]
    else:
        data = enc.gzip_member(enc.tar_archive(files))
        name, declared, fmt = "bundle.tar.gz", "application/gzip", ["gzip", "tar.gz", "tgz", "tar"]
    name = case.get("file", name)
    return {name: data}, {name: declared}, qs + [q("detect.format", fmt, ONE_OF), q("detect.family", "archive")]


def gen_gzip_csv(case, rng):
    t = table_values(rng, case)
    name = case.get("file", "values.csv.gz")
    data = enc.gzip_member(csv_bytes(t), name=name[:-3])
    return {name: data}, {name: "application/gzip"}, table_questions(t, rng)[:5]


GENERATORS = {"csv": gen_csv, "tsv": gen_csv, "json_records": gen_json_records, "ndjson": gen_json_records,
              "xlsx": gen_xlsx, "markdown": gen_markdown, "html": gen_html, "docx": gen_docx, "odt": gen_odt,
              "epub": gen_epub, "pdf": gen_pdf, "png": gen_png, "pgm": gen_pnm, "ppm": gen_pnm, "bmp": gen_bmp,
              "gif": gen_gif, "svg": gen_svg, "wav": gen_wav, "obj": gen_mesh, "stl_ascii": gen_mesh,
              "stl_binary": gen_mesh, "ply": gen_mesh, "ply_binary": gen_mesh, "geojson": gen_geojson, "gpx": gen_gpx,
              "json_tree": gen_json_tree, "xml_tree": gen_xml_tree, "py": gen_code, "py_zip": gen_code,
              "ipynb": gen_ipynb, "npy": gen_npy, "npz": gen_npz, "zip": gen_archive, "tar": gen_archive,
              "tgz": gen_archive, "csv_gz": gen_gzip_csv}


def build(family: str, split: str, cases, generators) -> list[Task]:
    out = []
    for case in cases:
        files, declared, questions = generators[case["fmt"]](case, rng_for(case["seed"]))
        for question, expect, tolerance, kind in questions:
            out.append(Task(f"{family}/{split}/{case['name']}/{question}", family, files, declared, question,
                            expect, tolerance, kind))
    return out


def tasks(split: str) -> list[Task]:
    check_split(split)
    return build("intake", split, INTAKE[split], GENERATORS)


def score(subject, split: str) -> dict:
    return default_score("intake", tasks, subject, split)
