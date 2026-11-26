"""Office packages and e-books to `Document` (lane B1): DOCX, ODT, PPTX, ODP, EPUB.

All are ZIP packages read with lane K's `safe.SafeZip` (member count checked first, sizes bounded, CRC checked) and
XML parts read with `safe.parse_xml` (DTDs and entities refused, nodes and depth bounded; the element bound also
follows from the bytes left, see `safe_b1.element_limits`). Nothing is executed:
macros (`vbaProject.bin`, ODF `Basic/`) are never read, only reported. Embedded media are listed as figure blocks
naming their member (`ref = {"alt", "caption", "member", "address"}`); their bytes are not decoded here.

- DOCX: `word/document.xml` in body order. Headings from the paragraph style (a style named "heading N" or an
  outline level) and "Title"; list items from numbering (`numPr`, level `ilvl + 1`); tables (`w:tbl`) as `Table`;
  pictures from `wp:docPr` (alt text) and `a:blip` (member through the relationships). Tracked deletions are
  left out, insertions kept; field codes are left out, their cached results kept. The title is `dc:title`.
  Not read (noted): footnotes, endnotes, comments, headers, footers, text boxes' layout, styles' appearance.
- ODT: `content.xml` `office:text`: `text:h` (outline level), `text:p`, nested `text:list`, `table:table`,
  `draw:frame`/`draw:image` (member, `svg:title`/`svg:desc` as alt). Notes (`text:note`) are left out (noted).
- PPTX: slides in `p:sldIdLst` order. Each slide is a section: a level-1 heading holding the title placeholder's
  words (or "Slide N" when the slide has no title; `ref = {"slide": N}` either way), body placeholder paragraphs
  as list items (level from `a:pPr lvl`), other text boxes as paragraphs, tables, pictures, and the speaker
  notes as `note` blocks.
- ODP: `draw:page` in order, the same section rule (title frame, else "Slide N"), notes as `note` blocks.
- EPUB: `META-INF/container.xml` -> the package document; XHTML items in spine order, each read by the HTML
  reader of `documents.py`; image addresses that resolve inside the package become member names. Resources listed
  in `META-INF/encryption.xml` are not decrypted (noted and skipped).
"""
from __future__ import annotations

import posixpath
import re

from ..safe import Deadline, SafeZip, local, parse_xml
from ..safe_b1 import DecodeBudget, charge_xml, decode_text, element_limits
from ..types import Block, Malformed, Refused
from . import Adapter
from .documents import _figure, _finish, html_blocks
from .tables import table_from_rows

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
P = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
PKG_REL = "{http://schemas.openxmlformats.org/package/2006/relationships}"
DC = "{http://purl.org/dc/elements/1.1/}"
TEXT = "{urn:oasis:names:tc:opendocument:xmlns:text:1.0}"
TABLE = "{urn:oasis:names:tc:opendocument:xmlns:table:1.0}"
DRAW = "{urn:oasis:names:tc:opendocument:xmlns:drawing:1.0}"
OFFICE = "{urn:oasis:names:tc:opendocument:xmlns:office:1.0}"
XLINK = "{http://www.w3.org/1999/xlink}"
SVG = "{urn:oasis:names:tc:opendocument:xmlns:svg-compatible:1.0}"
PRES = "{urn:oasis:names:tc:opendocument:xmlns:presentation:1.0}"
MAX_REPEAT = 1024          # ODF repeated rows/columns materialised at most this many times each


class Package:
    """One ZIP package being read: the reader, the budget, the deadline and the notes."""

    def __init__(self, data: bytes, limits):
        self.limits = limits
        self.deadline = Deadline(limits.max_seconds)
        self.budget = DecodeBudget(limits)
        self.zip = SafeZip(data, limits, deadline=self.deadline)
        self.notes: list[str] = []
        self.partial = False
        self.sizes = {e.name: e.size for e in self.zip.entries}
        names = set(self.sizes)
        if any(n.lower().endswith("vbaproject.bin") for n in names) or any(n.startswith("Basic/") for n in names):
            self.notes.append("the package holds macros: they are never read or run")

    def has(self, name: str | None) -> bool:
        return name is not None and name in self.sizes

    def raw(self, name: str, required: bool = True) -> bytes | None:
        """One member's bytes through `SafeZip.read` (ratio, declared size, CRC), charged to the shared budget."""
        if not self.has(name):
            if required:
                raise Malformed(f"the package has no member {name}")
            return None
        data = self.zip.read(name, max_bytes=self.budget.bytes_left)
        self.budget.take_bytes(len(data))
        return data

    def xml(self, name: str, required: bool = True):
        data = self.raw(name, required)
        if data is None:
            return None
        root = parse_xml(data, element_limits(self.limits, self.budget), deadline=self.deadline)
        charge_xml(self.budget, root)
        return root

    def rels(self, part: str) -> dict[str, tuple[str | None, str | None]]:
        """Relationship id -> (member name inside the package, external address) for an OOXML part."""
        folder, base = posixpath.split(part)
        root = self.xml(posixpath.join(folder, "_rels", base + ".rels"), required=False)
        out = {}
        if root is None:
            return out
        for rel in root.iter(PKG_REL + "Relationship"):
            target = rel.get("Target") or ""
            if rel.get("TargetMode") == "External":
                out[rel.get("Id")] = (None, target)
            else:
                out[rel.get("Id")] = (resolve_member(folder, target), None)
        return out

    def emit(self, blocks: list, block: Block) -> None:
        if self.partial:
            return
        if not self.budget.fits_elements(1):
            self.partial = True
            self.notes.append(f"stopped after {len(blocks)} blocks: the element limit was reached")
            return
        self.budget.take_elements(1)
        blocks.append(block)


def resolve_member(folder: str, target: str) -> str | None:
    """A relative reference inside the package, as a member name (None when it climbs out). Names stay data."""
    if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", target):
        return None
    joined = posixpath.normpath(posixpath.join(folder, target.split("#")[0])) if not target.startswith("/") \
        else posixpath.normpath(target.lstrip("/"))
    if joined.startswith("..") or joined.startswith("/"):
        return None
    return joined


def _table(pkg: Package, rows: list[list[str]], blocks: list, *, header=None, title=None) -> None:
    rows = [r for r in rows if r]
    if not rows:
        return
    try:
        table, notes = table_from_rows(rows, header=header, title=title)
    except Refused:
        pkg.notes.append("a table was too wide to keep: its cells are kept as text")
        pkg.emit(blocks, Block("paragraph", 0, "\n".join("\t".join(r) for r in rows)))
        return
    pkg.notes.extend(notes)
    pkg.emit(blocks, Block("table", 0, title or "", table))


def _core_title(pkg: Package, name: str = "docProps/core.xml") -> str | None:
    root = pkg.xml(name, required=False)
    if root is None:
        return None
    node = root.find(f".//{DC}title")
    return (node.text or "").strip() or None if node is not None else None


# ---------------------------------------------------------------------------------------------------------------
# DOCX


def _docx_styles(pkg: Package) -> dict[str, tuple[str, int | None]]:
    """style id -> (lower-case name, outline level or None)."""
    root = pkg.xml("word/styles.xml", required=False)
    out = {}
    if root is None:
        return out
    for style in root.iter(W + "style"):
        sid = style.get(W + "styleId")
        name_el = style.find(W + "name")
        name = (name_el.get(W + "val") if name_el is not None else sid or "").lower()
        lvl = style.find(f"{W}pPr/{W}outlineLvl")
        out[sid] = (name, int(lvl.get(W + "val")) if lvl is not None and (lvl.get(W + "val") or "").isdigit() else None)
    return out


def _docx_run_text(el, rels, figures) -> list[str]:
    """Text of a paragraph's content in document order; pictures collected into `figures`."""
    out: list[str] = []
    stack = [el]
    order = []
    while stack:                                   # explicit pre-order walk (no recursion)
        node = stack.pop()
        order.append(node)
        tag = node.tag
        if tag in (W + "del", W + "instrText", W + "delText", W + "footnoteReference", W + "endnoteReference",
                   W + "commentReference"):
            order.pop()
            continue
        stack.extend(reversed(list(node)))
    for node in order:
        tag = node.tag
        if tag == W + "t":
            out.append(node.text or "")
        elif tag == W + "tab":
            out.append("\t")
        elif tag in (W + "br", W + "cr"):
            out.append("\n")
        elif local(tag) == "docPr":
            figures.append(_figure(alt=node.get("descr") or None, caption=node.get("title") or None))
        elif tag == A + "blip":
            member, address = rels.get(node.get(R + "embed") or node.get(R + "link"), (None, None))
            if figures:
                figures[-1]["member"], figures[-1]["address"] = member, address
            else:
                figures.append(_figure(member=member, address=address))
        elif local(tag) == "imagedata":                # VML pictures
            member, address = rels.get(node.get(R + "id"), (None, None))
            figures.append(_figure(alt=node.get("{urn:schemas-microsoft-com:office:office}title"), member=member,
                                   address=address))
    return out


def _docx_paragraph(pkg, p, styles, rels, blocks, state) -> None:
    figures: list[dict] = []
    text = "".join(_docx_run_text(p, rels, figures)).strip()
    ppr = p.find(W + "pPr")
    kind, level = "paragraph", 0
    if ppr is not None:
        style = ppr.find(W + "pStyle")
        sid = style.get(W + "val") if style is not None else None
        name, outline = styles.get(sid, ((sid or "").lower(), None))
        direct = ppr.find(W + "outlineLvl")
        if direct is not None and (direct.get(W + "val") or "").isdigit():
            outline = int(direct.get(W + "val"))
        m = re.match(r"heading\s*(\d)", name or "")
        if name == "title":
            if text and state.get("title") is None:
                state["title"] = text
            kind, level = "heading", 1
        elif m:
            kind, level = "heading", int(m.group(1))
        elif outline is not None and outline < 9:
            kind, level = "heading", outline + 1
        elif ppr.find(W + "numPr") is not None:
            ilvl = ppr.find(f"{W}numPr/{W}ilvl")
            kind = "list_item"
            level = int(ilvl.get(W + "val")) + 1 if ilvl is not None and (ilvl.get(W + "val") or "").isdigit() else 1
    if text:
        pkg.emit(blocks, Block(kind, level, text))
    for f in figures:
        pkg.emit(blocks, Block("figure", 0, f["caption"] or f["alt"] or "", f))


def _docx_table(pkg, tbl, rels, blocks) -> None:
    rows = []
    spans = False
    for tr in tbl.findall(W + "tr"):
        row = []
        for tc in tr.findall(W + "tc"):
            if tc.find(f"{W}tcPr/{W}gridSpan") is not None or tc.find(f"{W}tcPr/{W}vMerge") is not None:
                spans = True
            paras = []
            for p in tc.iter(W + "p"):
                paras.append("".join(_docx_run_text(p, rels, [])).strip())
            row.append("\n".join(x for x in paras if x))
        rows.append(row)
    if spans:
        pkg.notes.append("merged table cells are placed in the first cell they cover")
    if tbl.find(f".//{W}tc//{W}tbl") is not None:
        pkg.notes.append("nested tables are flattened into the cell that holds them")
    _table(pkg, rows, blocks)


def read_docx(data, detected, limits):
    pkg = Package(data, limits)
    root = pkg.xml("word/document.xml")
    styles = _docx_styles(pkg)
    rels = pkg.rels("word/document.xml")
    body = root.find(W + "body")
    if body is None:
        raise Malformed("word/document.xml has no body")
    blocks: list[Block] = []
    state = {"title": None}
    stack = list(reversed(list(body)))
    count = 0
    while stack and not pkg.partial:
        count += 1
        if count % 256 == 0:
            pkg.deadline.check()
        el = stack.pop()
        if el.tag == W + "p":
            _docx_paragraph(pkg, el, styles, rels, blocks, state)
        elif el.tag == W + "tbl":
            _docx_table(pkg, el, rels, blocks)
        elif el.tag in (W + "sdt", W + "sdtContent", W + "customXml", W + "ins", W + "smartTag"):
            stack.extend(reversed(list(el)))
        elif el.tag == W + "sectPr":
            continue
    title = _core_title(pkg) or state["title"]
    names = set(pkg.sizes)
    unread = [n for n in ("word/footnotes.xml", "word/endnotes.xml", "word/comments.xml") if n in names]
    if any(n.startswith("word/header") or n.startswith("word/footer") for n in names):
        unread.append("headers/footers")
    notes = pkg.notes + ["DOCX: tracked deletions and field codes are left out; cached field results are kept"]
    if unread:
        notes.append("not read: " + ", ".join(unread))
    return _finish(detected, blocks, title, notes, pkg.partial)


# ---------------------------------------------------------------------------------------------------------------
# ODF (ODT, ODP) and the ODF table reader shared with ODS


def odf_text(el, skip_notes: bool = True) -> str:
    """Words of an ODF text element: spans, links, spaces (`text:s`), tabs and line breaks."""
    out: list[str] = []

    def walk(node, depth=0):
        if depth > 64:
            return
        if node.text:
            out.append(node.text)
        for child in node:
            tag = child.tag
            if tag == TEXT + "s":
                count = child.get(TEXT + "c") or "1"
                out.append(" " * min(int(count) if count.isdigit() else 1, 64))
            elif tag == TEXT + "tab":
                out.append("\t")
            elif tag == TEXT + "line-break":
                out.append("\n")
            elif tag == TEXT + "note" and skip_notes:
                pass
            elif tag in (DRAW + "frame", DRAW + "a"):
                pass
            else:
                walk(child, depth + 1)
            if child.tail:
                out.append(child.tail)
    walk(el)
    return "".join(out)


def odf_table_rows(table, budget: DecodeBudget, notes: list[str]) -> list[list]:
    """Rows of strings of a `table:table`, repeated rows and columns expanded at most MAX_REPEAT times; trailing
    empty rows and columns (the usual way ODF fills a sheet) dropped. Typed values: see `sheets.py`."""
    return odf_table_cells(table, budget, notes, typed=False)


def odf_table_cells(table, budget: DecodeBudget, notes: list[str], *, typed: bool) -> list[list]:
    from .tables import TimeValue
    rows: list[list] = []
    capped = False
    for row in table.iter(TABLE + "table-row"):
        rep = row.get(TABLE + "number-rows-repeated") or "1"
        rep = int(rep) if rep.isdigit() else 1
        cells: list = []
        for cell in row:
            if local(cell.tag) not in ("table-cell", "covered-table-cell"):
                continue
            crep = cell.get(TABLE + "number-columns-repeated") or "1"
            crep = int(crep) if crep.isdigit() else 1
            if crep > MAX_REPEAT:
                capped = True
                crep = MAX_REPEAT
            value: object
            vtype = cell.get(OFFICE + "value-type")
            words = "\n".join(odf_text(p) for p in cell.findall(TEXT + "p"))
            if typed and vtype in ("float", "percentage", "currency"):
                try:
                    value = float(cell.get(OFFICE + "value"))
                    if value.is_integer() and vtype == "float" and "." not in (cell.get(OFFICE + "value") or ""):
                        value = int(value)
                except (TypeError, ValueError):
                    value = words
            elif typed and vtype == "boolean":
                value = (cell.get(OFFICE + "boolean-value") or "").lower() == "true"
            elif typed and vtype == "date":
                from .tables import _iso
                parsed = _iso(cell.get(OFFICE + "date-value") or "")
                value = TimeValue(parsed[0]) if parsed else words
            else:
                value = words
            cells.extend([value] * crep)
        while cells and (cells[-1] == "" or cells[-1] is None):
            cells.pop()
        if rep > MAX_REPEAT:
            capped = capped or bool(cells)
            rep = MAX_REPEAT if cells else 1
        if not budget.fits_elements(len(cells) * rep):
            notes.append(f"a table was cut after {len(rows)} rows: the element limit was reached")
            break
        budget.take_elements(len(cells) * rep)
        rows.extend([list(cells) for _ in range(rep)])
    while rows and not any(c not in ("", None) for c in rows[-1]):
        rows.pop()
    if capped:
        notes.append(f"partial: repeated rows or columns were expanded at most {MAX_REPEAT} times")
    return rows


def _odf_frame(pkg, frame, blocks, folder="") -> None:
    title = frame.find(SVG + "title")
    desc = frame.find(SVG + "desc")
    for image in frame.iter(DRAW + "image"):
        href = image.get(XLINK + "href") or ""
        member = resolve_member(folder, href)
        ref = _figure(alt=(desc.text if desc is not None else None) or (title.text if title is not None else None),
                      caption=title.text if title is not None else None, member=member,
                      address=None if member else href)
        pkg.emit(blocks, Block("figure", 0, ref["caption"] or ref["alt"] or "", ref))


def _odf_body(pkg, el, blocks, list_level=0, depth=0) -> None:
    if depth > 64 or pkg.partial:
        return
    for child in el:
        if pkg.partial:
            return
        tag = child.tag
        if tag == TEXT + "h":
            lvl = child.get(TEXT + "outline-level") or "1"
            text = odf_text(child).strip()
            if text:
                pkg.emit(blocks, Block("heading", int(lvl) if lvl.isdigit() else 1, text))
            for frame in child.iter(DRAW + "frame"):
                _odf_frame(pkg, frame, blocks)
        elif tag == TEXT + "p":
            text = odf_text(child).strip()
            if text:
                pkg.emit(blocks, Block("list_item" if list_level else "paragraph", list_level, text))
            for frame in child.iter(DRAW + "frame"):
                _odf_frame(pkg, frame, blocks)
        elif tag == TEXT + "list":
            for item in child:
                if local(item.tag) in ("list-item", "list-header"):
                    _odf_body(pkg, item, blocks, list_level + 1, depth + 1)
        elif tag == TABLE + "table":
            _table(pkg, odf_table_rows(child, pkg.budget, pkg.notes), blocks)
            pkg.partial = pkg.partial or any(n.startswith("partial: repeated") for n in pkg.notes)
        elif tag in (TEXT + "section", TEXT + "index-body", TEXT + "table-of-content", DRAW + "text-box"):
            _odf_body(pkg, child, blocks, list_level, depth + 1)
        elif tag == DRAW + "frame":
            _odf_frame(pkg, child, blocks)
            for box in child.findall(DRAW + "text-box"):
                _odf_body(pkg, box, blocks, list_level, depth + 1)


def read_odt(data, detected, limits):
    pkg = Package(data, limits)
    root = pkg.xml("content.xml")
    text = root.find(f"{OFFICE}body/{OFFICE}text")
    if text is None:
        raise Malformed("content.xml has no office:text body")
    blocks: list[Block] = []
    _odf_body(pkg, text, blocks)
    meta = pkg.xml("meta.xml", required=False)
    title = None
    if meta is not None:
        node = meta.find(f".//{DC}title")
        title = (node.text or "").strip() or None if node is not None else None
    notes = pkg.notes + ["ODT: footnotes and endnotes (text:note) are left out"]
    return _finish(detected, blocks, title, notes, pkg.partial)


def _section(pkg, blocks, number: int, title: str | None) -> None:
    pkg.emit(blocks, Block("heading", 1, title or f"Slide {number}", {"slide": number}))


def read_odp(data, detected, limits):
    pkg = Package(data, limits)
    root = pkg.xml("content.xml")
    pres = root.find(f"{OFFICE}body/{OFFICE}presentation")
    if pres is None:
        raise Malformed("content.xml has no office:presentation body")
    blocks: list[Block] = []
    untitled = 0
    for n, page in enumerate(pres.findall(DRAW + "page"), 1):
        if pkg.partial:
            break
        if n > limits.max_members:
            pkg.notes.append(f"stopped after {limits.max_members} slides")
            pkg.partial = True
            break
        title = None
        for frame in page.findall(DRAW + "frame"):
            if frame.get(PRES + "class") == "title":
                title = " ".join(odf_text(p).strip() for p in frame.iter(TEXT + "p")).strip() or None
        untitled += title is None
        _section(pkg, blocks, n, title)
        for frame in page.findall(DRAW + "frame"):
            if frame.get(PRES + "class") == "title":
                continue
            _odf_frame(pkg, frame, blocks)
            for box in frame.findall(DRAW + "text-box"):
                _odf_body(pkg, box, blocks)
            for table in frame.findall(TABLE + "table"):
                _table(pkg, odf_table_rows(table, pkg.budget, pkg.notes), blocks)
        notes_el = page.find(PRES + "notes")
        if notes_el is not None:
            for p in notes_el.iter(TEXT + "p"):
                words = odf_text(p).strip()
                if words:
                    pkg.emit(blocks, Block("note", 0, words))
    notes = pkg.notes
    if untitled:
        notes = notes + [f"{untitled} slide(s) have no title: their section is headed 'Slide N'"]
    return _finish(detected, blocks, None, notes, pkg.partial)


# ---------------------------------------------------------------------------------------------------------------
# PPTX


def _pptx_paragraphs(body) -> list[tuple[int, str]]:
    out = []
    for p in body.findall(A + "p"):
        parts = []
        for node in p.iter():
            if node.tag == A + "t":
                parts.append(node.text or "")
            elif node.tag == A + "br":
                parts.append("\n")
        ppr = p.find(A + "pPr")
        lvl = ppr.get("lvl") if ppr is not None else None
        out.append((int(lvl) if lvl and lvl.isdigit() else 0, "".join(parts).strip()))
    return out


def _pptx_shapes(pkg, tree, rels, blocks, depth=0) -> None:
    if depth > 32:
        return
    for shape in tree:
        if pkg.partial:
            return
        tag = shape.tag
        if tag == P + "grpSp":
            _pptx_shapes(pkg, shape, rels, blocks, depth + 1)
        elif tag == P + "sp":
            ph = shape.find(f"{P}nvSpPr/{P}nvPr/{P}ph")
            kind = ph.get("type") if ph is not None else None
            if kind in ("title", "ctrTitle"):
                continue
            body = shape.find(P + "txBody")
            if body is None:
                continue
            bullets = ph is not None and kind in (None, "body", "obj")
            for lvl, text in _pptx_paragraphs(body):
                if text:
                    pkg.emit(blocks, Block("list_item", lvl + 1, text) if bullets else Block("paragraph", 0, text))
        elif tag == P + "graphicFrame":
            tbl = shape.find(f".//{A}tbl")
            if tbl is not None:
                rows = [[" ".join(t for _, t in _pptx_paragraphs(tc.find(A + "txBody")) if t)
                         if tc.find(A + "txBody") is not None else "" for tc in tr.findall(A + "tc")]
                        for tr in tbl.findall(A + "tr")]
                _table(pkg, rows, blocks)
        elif tag == P + "pic":
            pr = shape.find(f"{P}nvPicPr/{P}cNvPr")
            blip = shape.find(f".//{A}blip")
            member, address = rels.get(blip.get(R + "embed") or blip.get(R + "link"), (None, None)) \
                if blip is not None else (None, None)
            ref = _figure(alt=pr.get("descr") if pr is not None else None, member=member, address=address)
            pkg.emit(blocks, Block("figure", 0, ref["alt"] or "", ref))


def _pptx_title(tree) -> str | None:
    for shape in tree.iter(P + "sp"):
        ph = shape.find(f"{P}nvSpPr/{P}nvPr/{P}ph")
        if ph is not None and ph.get("type") in ("title", "ctrTitle"):
            body = shape.find(P + "txBody")
            if body is not None:
                text = " ".join(t for _, t in _pptx_paragraphs(body) if t).strip()
                return text or None
    return None


def read_pptx(data, detected, limits):
    pkg = Package(data, limits)
    pres = pkg.xml("ppt/presentation.xml")
    prels = pkg.rels("ppt/presentation.xml")
    blocks: list[Block] = []
    lst = pres.find(P + "sldIdLst")
    slides = [prels.get(s.get(R + "id"), (None, None))[0] for s in (lst if lst is not None else [])]
    untitled = 0
    for n, part in enumerate(slides, 1):
        if pkg.partial:
            break
        pkg.deadline.check()
        if n > limits.max_members:
            pkg.notes.append(f"stopped after {limits.max_members} slides")
            pkg.partial = True
            break
        if not pkg.has(part):
            pkg.notes.append(f"slide {n} is listed but its part is missing")
            continue
        root = pkg.xml(part)
        rels = pkg.rels(part)
        tree = root.find(f"{P}cSld/{P}spTree")
        if tree is None:
            continue
        title = _pptx_title(tree)
        untitled += title is None
        _section(pkg, blocks, n, title)
        _pptx_shapes(pkg, tree, rels, blocks)
        for target, _ in rels.values():
            if target and "notesSlide" in target and pkg.has(target):
                notes_root = pkg.xml(target)
                for shape in notes_root.iter(P + "sp"):
                    ph = shape.find(f"{P}nvSpPr/{P}nvPr/{P}ph")
                    body = shape.find(P + "txBody")
                    if ph is not None and ph.get("type") == "body" and body is not None:
                        for _, text in _pptx_paragraphs(body):
                            if text:
                                pkg.emit(blocks, Block("note", 0, text))
    notes = pkg.notes + ["PPTX: slide layout, masters, animations and charts' data are not read"]
    if untitled:
        notes.append(f"{untitled} slide(s) have no title: their section is headed 'Slide N'")
    return _finish(detected, blocks, _core_title(pkg), notes, pkg.partial)


# ---------------------------------------------------------------------------------------------------------------
# EPUB

OPF = "{http://www.idpf.org/2007/opf}"
CONTAINER = "{urn:oasis:names:tc:opendocument:xmlns:container}"
ENC = "{http://www.w3.org/2001/04/xmlenc#}"


def read_epub(data, detected, limits):
    pkg = Package(data, limits)
    container = pkg.xml("META-INF/container.xml")
    rootfile = container.find(f".//{CONTAINER}rootfile")
    opf_name = rootfile.get("full-path") if rootfile is not None else None
    if not opf_name or resolve_member("", opf_name) is None:
        raise Malformed("META-INF/container.xml names no package document")
    opf = pkg.xml(resolve_member("", opf_name))
    folder = posixpath.dirname(resolve_member("", opf_name))
    title_el = opf.find(f".//{DC}title")
    title = (title_el.text or "").strip() or None if title_el is not None else None
    manifest = {}
    for item in opf.iter(OPF + "item"):
        manifest[item.get("id")] = (resolve_member(folder, item.get("href") or ""), item.get("media-type") or "")
    encrypted = set()
    enc = pkg.xml("META-INF/encryption.xml", required=False)
    if enc is not None:
        for ref in enc.iter(ENC + "CipherReference"):
            uri = resolve_member("", ref.get("URI") or "")
            if uri:
                encrypted.add(uri)
        pkg.notes.append(f"{len(encrypted)} resource(s) are encrypted (META-INF/encryption.xml): not decrypted")
    blocks: list[Block] = []
    spine = opf.find(OPF + "spine")
    items = [i.get("idref") for i in (spine.findall(OPF + "itemref") if spine is not None else [])]
    for n, idref in enumerate(items):
        if pkg.partial:
            break
        if n >= limits.max_members:
            pkg.notes.append(f"stopped after {limits.max_members} spine items")
            pkg.partial = True
            break
        member, media = manifest.get(idref, (None, ""))
        if member is None or "html" not in media:
            continue
        if member in encrypted:
            continue
        raw = pkg.raw(member, required=False)
        if raw is None:
            pkg.notes.append("a spine item is missing from the package")
            continue
        text, tnotes = decode_text(raw)
        pkg.notes.extend(tnotes)
        item_blocks, _, hnotes = html_blocks(text, limits)
        pkg.notes.extend(hnotes)
        item_folder = posixpath.dirname(member)
        for block in item_blocks:
            if block.kind == "figure" and isinstance(block.ref, dict) and block.ref.get("address"):
                inside = resolve_member(item_folder, block.ref["address"])
                if pkg.has(inside):
                    ref = dict(block.ref, member=inside, address=None)
                    block = Block("figure", block.level, block.text, ref)
            pkg.emit(blocks, block)
    notes = pkg.notes + ["EPUB: spine order; navigation documents, styles and fonts are not read"]
    return _finish(detected, blocks, title, notes, pkg.partial)


# ---------------------------------------------------------------------------------------------------------------
# Adapters


def _adapter(adapter_name, fmts, reader):
    class _A(Adapter):
        name = adapter_name
        formats = fmts
        families = ("document",)

        def decode(self, data, detected, limits):
            return reader(data, detected, limits)
    _A.__name__ = adapter_name.replace("-", "_")
    return _A


DocxAdapter = _adapter("b1-docx", ("docx",), read_docx)
OdtAdapter = _adapter("b1-odt", ("odt",), read_odt)
PptxAdapter = _adapter("b1-pptx", ("pptx",), read_pptx)
OdpAdapter = _adapter("b1-odp", ("odp",), read_odp)
EpubAdapter = _adapter("b1-epub", ("epub",), read_epub)

ADAPTERS = (DocxAdapter, OdtAdapter, PptxAdapter, OdpAdapter, EpubAdapter)

__all__ = ["ADAPTERS", "DocxAdapter", "EpubAdapter", "OdpAdapter", "OdtAdapter", "Package", "PptxAdapter",
           "odf_table_cells", "odf_table_rows", "odf_text", "read_docx", "read_epub", "read_odp", "read_odt",
           "read_pptx", "resolve_member"]
