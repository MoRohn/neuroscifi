"""Markdown, HTML and RTF to `Document` (lane B1). Standard library only; `webcortex.extract` is not used.

- Markdown, by lines: front matter (a leading `---` block; its `title:` becomes the title), ATX and setext
  headings, fenced code (``` or ~~~, the info word kept as the language), `$$` equations, pipe tables (a header row
  followed by a `---` separator row) as `Table`, list items (level by indentation), block quotes as paragraphs,
  images as figure blocks with their alt text, paragraphs. Links are kept as text: `[words](address)` becomes
  `words (address)`; nothing is loaded. Inline emphasis markers are kept as written.
- HTML, through `html.parser`: headings, paragraphs, list items (level by list nesting), `pre` as code, `math` as
  an equation, tables as `Table` (header cells `th`; `caption` is the title; nested tables are flattened into their
  cell, noted), `img` as a figure block with its alt text (and the `figcaption` of an enclosing `figure`). Dropped
  with their content: script, style, template, noscript, iframe, object, embed, svg, and every element marked
  `hidden`, `aria-hidden="true"` or styled `display:none` / `visibility:hidden` (counted in a note).
- RTF, by its tokens: text and paragraph structure only (`\\par`, `\\page`); `\\uN` and `\\'hh` characters in the
  declared ANSI code page; the `\\info` title; font, colour and style tables, pictures, objects, headers, footers
  and other ignorable destinations are skipped. Tables become plain lines (cells separated by TAB): said in notes.

Figure blocks carry `ref = {"alt", "caption", "member", "address"}`: words and names as text, never loaded.
"""
from __future__ import annotations

import re
from html.parser import HTMLParser

from ..safe import Deadline, injection_notes
from ..safe_b1 import DecodeBudget, decode_text
from ..types import Block, Decoded, Document, Limits, Malformed, Refused
from . import Adapter
from .tables import table_from_rows

FEED_CHUNK = 1 << 16
MAX_RTF_DEPTH = 256
# Lane FX3 (class H-A): nesting of HTML elements and of Markdown quote markers is bounded as RTF groups, trees,
# JSON and XML are: a document nested deeper is refused whole (rule max_depth), never read flat.
MAX_MARKUP_DEPTH = 256
# Elements whose end tag HTML lets a writer leave out: they are not counted as nesting (an honest page may leave a
# thousand <p> or <li> open).
_OPTIONAL_END = {"p", "li", "dt", "dd", "tr", "td", "th", "option", "optgroup", "thead", "tbody", "tfoot",
                 "colgroup", "caption", "rb", "rt", "rp", "rtc", "head", "body", "html"}
_QUOTE_MARKERS = re.compile(r"[ \t]*((?:>[ \t]?)+)")

_ATX = re.compile(r"^ {0,3}(#{1,6})(?:[ \t]+(.*))?$")
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})\s*([\w+.#-]*)")
_LIST = re.compile(r"^(\s*)([-*+]|\d{1,9}[.)])\s+(.*)$")
_HR = re.compile(r"^ {0,3}([-*_])(\s*\1){2,}\s*$")
_TABLE_SEP = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$")
# Bounded, possessive runs that cannot cross a bracket: every pattern below runs in linear time on hostile text.
_IMAGE = re.compile(r"!\[([^\[\]]{0,1000}+)\]\( {0,8}+<?([^)\s<>\[\]]{0,2000}+)>?(?: {1,8}+\"([^\"]{0,1000}+)\")? {0,8}+\)")
_LINK = re.compile(r"(?<!!)\[([^\[\]]{1,1000}+)\]\( {0,8}+<?([^)\s<>\[\]]{0,2000}+)>?(?: {1,8}+\"[^\"]{0,1000}+\")? {0,8}+\)")
_AUTOLINK = re.compile(r"<((?:https?|mailto|ftp):[^>\s]+)>")
_WS = re.compile(r"\s+")


def _figure(alt=None, caption=None, member=None, address=None) -> dict:
    return {"alt": alt or None, "caption": caption or None, "member": member, "address": address or None}


def _finish(detected, blocks, title, notes, partial) -> Decoded:
    doc = Document(tuple(blocks), title)
    return Decoded(detected, doc, tuple(dict.fromkeys(list(notes) + list(injection_notes(doc.text())))), partial)


# ---------------------------------------------------------------------------------------------------------------
# Markdown


def _inline(text: str) -> tuple[str, list[dict]]:
    """Links to 'words (address)'; images out to figure refs (their alt text stays in the words)."""
    figures = [_figure(alt=m.group(1), caption=m.group(3), address=m.group(2)) for m in _IMAGE.finditer(text)]
    text = _IMAGE.sub(lambda m: m.group(1), text)
    text = _LINK.sub(lambda m: f"{m.group(1)} ({m.group(2)})" if m.group(2) else m.group(1), text)
    text = _AUTOLINK.sub(lambda m: m.group(1), text)
    return text, figures


def _cells(line: str) -> list[str]:
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|") and not line.endswith("\\|"):
        line = line[:-1]
    return [c.strip().replace("\\|", "|") for c in re.split(r"(?<!\\)\|", line)]


def markdown_blocks(text: str, *, budget: DecodeBudget | None = None, deadline: Deadline | None = None
                    ) -> tuple[list[Block], str | None, list[str], bool]:
    """(blocks, title, notes, partial) for Markdown text by the module's rules."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    blocks: list[Block] = []
    notes: list[str] = []
    title, i, para, partial = None, 0, [], False
    indents: list[int] = []

    def emit(block):
        nonlocal partial
        if budget is not None and not budget.fits_elements(1):
            partial = True
            return False
        if budget is not None:
            budget.take_elements(1)
        blocks.append(block)
        return True

    def flush():
        if para:
            words, figures = _inline(" ".join(s.strip() for s in para))
            emit(Block("paragraph", 0, words))
            for f in figures:
                emit(Block("figure", 0, f["caption"] or f["alt"] or "", f))
            para.clear()

    if lines and lines[0].strip() == "---":
        for j in range(1, min(len(lines), 200)):
            if lines[j].strip() in ("---", "..."):
                for ln in lines[1:j]:
                    m = re.match(r"^title:\s*(.+?)\s*$", ln)
                    if m:
                        title = m.group(1).strip("'\"")
                notes.append("front matter read for its title only")
                i = j + 1
                break
    while i < len(lines) and not partial:
        if deadline and i % 1024 == 0:
            deadline.check()
        line = lines[i]
        stripped = line.strip()
        fence = _FENCE.match(line)
        if fence:
            flush()
            mark, lang = fence.group(1), fence.group(2) or None
            body, i = [], i + 1
            while i < len(lines) and not lines[i].strip().startswith(mark[0] * len(mark)):
                body.append(lines[i])
                i += 1
            if i >= len(lines):
                notes.append("a code fence is never closed: read to the end")
            emit(Block("code", 0, "\n".join(body), {"language": lang} if lang else None))
            i += 1
            continue
        if stripped.startswith("$$"):
            flush()
            body = [stripped[2:]]
            if stripped.endswith("$$") and len(stripped) > 4:
                body = [stripped[2:-2]]
            else:
                i += 1
                while i < len(lines) and "$$" not in lines[i]:
                    body.append(lines[i])
                    i += 1
                if i < len(lines):
                    body.append(lines[i].split("$$")[0])
            emit(Block("equation", 0, "\n".join(b for b in body if b.strip()).strip()))
            i += 1
            continue
        atx = _ATX.match(line)
        if atx:
            flush()
            words = (atx.group(2) or "").strip()
            stripped_words = words.rstrip("#")            # a closing run of '#' is not part of the heading
            if stripped_words != words and (not stripped_words or stripped_words[-1] in " \t"):
                words = stripped_words.strip()
            emit(Block("heading", len(atx.group(1)), _inline(words)[0]))
            i += 1
            continue
        if para and re.match(r"^ {0,3}(=+|-+)\s*$", line) and not _LIST.match(para[-1]):
            level = 1 if stripped.startswith("=") else 2
            words = _inline(" ".join(s.strip() for s in para))[0]
            para.clear()
            emit(Block("heading", level, words))
            i += 1
            continue
        if "|" in line and i + 1 < len(lines) and _TABLE_SEP.match(lines[i + 1]) and "-" in lines[i + 1]:
            flush()
            rows = [_cells(line)]
            i += 2
            while i < len(lines) and "|" in lines[i] and lines[i].strip():
                rows.append(_cells(lines[i]))
                i += 1
            rows = [[_inline(c)[0] for c in r] for r in rows]
            table, tnotes = table_from_rows(rows, header=True)
            notes.extend(tnotes)
            emit(Block("table", 0, "", table))
            continue
        if _HR.match(line):
            flush()
            i += 1
            continue
        lm = _LIST.match(line)
        if lm:
            flush()
            indent = len(lm.group(1).expandtabs(4))
            while indents and indents[-1] > indent:
                indents.pop()
            if not indents or indents[-1] < indent:
                indents.append(indent)
            words, figures = _inline(lm.group(3))
            emit(Block("list_item", len(indents), words))
            for f in figures:
                emit(Block("figure", 0, f["caption"] or f["alt"] or "", f))
            i += 1
            continue
        if not stripped:
            flush()
            indents.clear()
            i += 1
            continue
        if line.startswith(("    ", "\t")) and not para and not indents:
            body = []
            while i < len(lines) and (lines[i].startswith(("    ", "\t")) or not lines[i].strip()):
                body.append(lines[i][4:] if lines[i].startswith("    ") else lines[i][1:])
                i += 1
            emit(Block("code", 0, "\n".join(body).rstrip("\n")))
            continue
        if stripped.startswith(">"):
            flush()
            quote = []
            while i < len(lines) and lines[i].strip().startswith(">"):
                markers = _QUOTE_MARKERS.match(lines[i])
                if markers and markers.group(1).count(">") > MAX_MARKUP_DEPTH:
                    raise Refused(f"the Markdown nests block quotes deeper than {MAX_MARKUP_DEPTH} levels",
                                  rule="max_depth")
                quote.append(lines[i].strip()[1:].strip())
                i += 1
            words, figures = _inline(" ".join(q for q in quote if q))
            emit(Block("paragraph", 1, words))
            continue
        para.append(line)
        i += 1
    flush()
    if partial:
        notes.append(f"stopped after {len(blocks)} blocks: the element limit was reached")
    return blocks, title, notes, partial


def read_markdown(data, detected, limits):
    text, notes = decode_text(data)
    blocks, title, more, partial = markdown_blocks(text, budget=DecodeBudget(limits), deadline=Deadline(limits.max_seconds))
    return _finish(detected, blocks, title, list(notes) + more + ["inline emphasis markers are kept as written"],
                   partial)


# ---------------------------------------------------------------------------------------------------------------
# HTML

_DROP = {"script", "style", "template", "noscript", "iframe", "object", "embed", "svg", "select", "textarea",
         "button", "canvas", "audio", "video"}
_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr", "param"}
_BLOCKS = {"p", "div", "section", "article", "header", "footer", "main", "aside", "nav", "address", "dd", "dt", "dl",
           "form", "fieldset", "details", "summary", "center", "body", "html", "hr", "ul", "ol", "figure"}
_HIDDEN_STYLE = re.compile(r"display\s*:\s*none|visibility\s*:\s*hidden", re.I)


class _HtmlReader(HTMLParser):
    def __init__(self, budget: DecodeBudget):
        super().__init__(convert_charrefs=True)
        self.budget = budget
        self.blocks: list[Block] = []
        self.title: str | None = None
        self.notes: list[str] = []
        self.partial = False
        self.buf: list[str] = []
        self.mode, self.level = "paragraph", 0
        self.skip_tag, self.skip_depth = None, 0
        self.hidden = 0
        self.in_title = False
        self.title_buf: list[str] = []
        self.list_depth = 0
        self.pre = 0
        self.math = 0
        self.tables: list[dict] = []
        self.figures: list[list] = []        # open <figure> elements: their figure refs
        self.blockquote = 0
        self.tags = 0
        self.nested_tables = 0
        self.depth = 0                       # open elements whose end tag is required (lane FX3, H-A)

    # -- helpers
    def _emit(self, block: Block) -> None:
        if not self.budget.fits_elements(1):
            self.partial = True
            return
        self.budget.take_elements(1)
        self.blocks.append(block)

    def _text(self) -> str:
        raw = "".join(self.buf)
        self.buf = []
        if self.pre:
            return raw.strip("\n")
        return "\n".join(_WS.sub(" ", part).strip() for part in raw.split("\n")).strip()

    def flush(self) -> None:
        if self.tables or self.mode == "figcaption":
            return
        text = self._text()
        if text:
            kind = self.mode
            self._emit(Block(kind, self.level if kind in ("heading", "list_item") else (1 if self.blockquote else 0),
                             text))

    def _cell_text(self) -> None:
        table = self.tables[-1]
        if table["cell"] is not None:
            table["cell"].append("".join(self.buf))
        self.buf = []

    # -- parser callbacks
    def handle_starttag(self, tag, attrs):
        self.tags += 1
        if tag not in _VOID and tag not in _OPTIONAL_END:
            self.depth += 1
            if self.depth > MAX_MARKUP_DEPTH:
                raise Refused(f"the HTML nests elements deeper than {MAX_MARKUP_DEPTH} levels", rule="max_depth")
        if not self.budget.fits_elements(1) or self.tags > self.budget.max_elements:
            self.partial = True
        if self.partial:
            return
        if self.skip_tag:
            if tag == self.skip_tag:
                self.skip_depth += 1
            return
        a = {k: (v or "") for k, v in attrs}
        hidden = ("hidden" in a or a.get("aria-hidden", "").lower() == "true"
                  or bool(_HIDDEN_STYLE.search(a.get("style", ""))))
        if tag == "title" and not self.title:
            self.in_title = True
            return
        if (tag in _DROP or hidden) and tag not in _VOID:
            if hidden:
                self.hidden += 1
            self.skip_tag, self.skip_depth = tag, 1
            return
        if hidden:
            self.hidden += 1
            return
        if tag == "br":
            self.buf.append("\n")
            return
        if tag == "img":
            ref = _figure(alt=a.get("alt"), address=a.get("src"))
            if self.tables:
                self.buf.append(a.get("alt", ""))
                return
            self.flush()
            if self.figures:
                self.figures[-1].append(ref)
            else:
                self._emit(Block("figure", 0, ref["alt"] or "", ref))
            return
        if tag == "table":
            if self.tables:
                self.nested_tables += 1
                return
            self.flush()
            self.tables.append({"rows": [], "row": None, "cell": None, "head": [], "caption": None, "in_cap": False})
            return
        if self.tables:
            table = self.tables[-1]
            if self.nested_tables:
                if tag in ("td", "th", "tr"):
                    self.buf.append(" ")
                return
            if tag == "tr":
                table["row"], table["heads"] = [], []
                table["rows"].append(table["row"])
                table.setdefault("row_heads", []).append(table["heads"])
            elif tag in ("td", "th"):
                if table["row"] is None:
                    table["row"], table["heads"] = [], []
                    table["rows"].append(table["row"])
                    table.setdefault("row_heads", []).append(table["heads"])
                self._cell_text()
                table["cell"] = []
                table["row"].append(table["cell"])
                table["heads"].append(tag == "th")
                if a.get("colspan", "1").strip() not in ("", "1"):
                    table["spans"] = True
            elif tag == "caption":
                self._cell_text()
                table["in_cap"] = True
            return
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self.flush()
            self.mode, self.level = "heading", int(tag[1])
        elif tag == "li":
            self.flush()
            self.mode, self.level = "list_item", max(1, self.list_depth)
        elif tag in ("ul", "ol"):
            self.flush()
            self.list_depth += 1
        elif tag == "pre":
            self.flush()
            self.pre += 1
        elif tag == "math":
            self.flush()
            self.math += 1
        elif tag == "blockquote":
            self.flush()
            self.blockquote += 1
        elif tag == "figure":
            self.flush()
            self.figures.append([])
        elif tag == "figcaption":
            self.flush()
            self.mode = "figcaption"
        elif tag in _BLOCKS:
            self.flush()
            if self.mode != "list_item" or tag != "p":
                self.mode, self.level = "paragraph", 0

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in _VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if tag not in _VOID and tag not in _OPTIONAL_END:
            self.depth = max(0, self.depth - 1)
        if self.partial:
            return
        if self.skip_tag:
            if tag == self.skip_tag:
                self.skip_depth -= 1
                if self.skip_depth <= 0:
                    self.skip_tag = None
            return
        if tag == "title" and self.in_title:
            self.in_title = False
            self.title = _WS.sub(" ", "".join(self.title_buf)).strip() or None
            return
        if self.tables:
            table = self.tables[-1]
            if tag == "table":
                if self.nested_tables:
                    self.nested_tables -= 1
                    return
                self._cell_text()
                self._close_table()
            elif self.nested_tables:
                return
            elif tag in ("td", "th"):
                self._cell_text()
                table["cell"] = None
            elif tag == "caption":
                table["caption"] = _WS.sub(" ", "".join(self.buf)).strip() or None
                self.buf = []
                table["in_cap"] = False
            return
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self.flush()
            self.mode, self.level = "paragraph", 0
        elif tag == "li":
            self.flush()
            self.mode, self.level = "paragraph", 0
        elif tag in ("ul", "ol"):
            self.flush()
            self.list_depth = max(0, self.list_depth - 1)
        elif tag == "pre":
            if self.pre:
                text = self._text()
                if text:
                    self._emit(Block("code", 0, text))
                self.pre -= 1
        elif tag == "math":
            text = self._text()
            if text:
                self._emit(Block("equation", 0, text))
            self.math = max(0, self.math - 1)
        elif tag == "blockquote":
            self.flush()
            self.blockquote = max(0, self.blockquote - 1)
        elif tag == "figcaption":
            caption = self._text()
            if self.figures and self.figures[-1]:
                self.figures[-1][-1]["caption"] = caption or None
            elif caption:
                self._emit(Block("paragraph", 0, caption))
            self.mode = "paragraph"
        elif tag == "figure":
            self.flush()
            if self.figures:
                for ref in self.figures.pop():
                    self._emit(Block("figure", 0, ref["caption"] or ref["alt"] or "", ref))
        elif tag in _BLOCKS or tag == "p":
            self.flush()

    def _close_table(self):
        table = self.tables.pop()
        rows = [[_WS.sub(" ", "".join(c)).strip() for c in r] for r in table["rows"] if r]
        heads = [h for h, r in zip(table.get("row_heads", []), table["rows"]) if r]
        if not rows:
            return
        header = True if heads and heads[0] and all(heads[0]) else None
        try:
            parsed, tnotes = table_from_rows(rows, header=header, title=table["caption"])
        except Refused:
            self.notes.append("a table was too wide to keep: its cells are kept as text")
            self._emit(Block("paragraph", 0, "\n".join("\t".join(r) for r in rows)))
            return
        if table.get("spans"):
            tnotes.append("cells spanning several columns are placed in the first column they cover")
        self.notes.extend(tnotes)
        self._emit(Block("table", 0, table["caption"] or "", parsed))

    def handle_data(self, data):
        if self.partial or self.skip_tag:
            return
        if self.in_title:
            self.title_buf.append(data)
            return
        self.buf.append(data)


def read_html(data, detected, limits):
    text, notes = decode_text(data)
    reader, deadline = _HtmlReader(DecodeBudget(limits)), Deadline(limits.max_seconds)
    for i in range(0, len(text), FEED_CHUNK):
        deadline.check()
        reader.feed(text[i:i + FEED_CHUNK])
        if reader.partial:
            break
    if not reader.partial:
        reader.close()
        while reader.tables:
            reader._cell_text()
            reader._close_table()
        reader.flush()
    notes = list(notes)
    if reader.hidden:
        notes.append(f"{reader.hidden} hidden element(s) skipped with their content")
    if reader.nested_tables or any("nested" in n for n in reader.notes):
        notes.append("nested tables are flattened into the cell that holds them")
    notes.append("scripts, styles, frames, embedded objects and inline SVG are dropped with their content")
    if reader.partial:
        notes.append(f"stopped after {len(reader.blocks)} blocks: the element limit was reached")
    return _finish(detected, reader.blocks, reader.title, notes + reader.notes, reader.partial)


def html_blocks(text: str, limits: Limits) -> tuple[list[Block], str | None, list[str]]:
    """Blocks of an HTML or XHTML text (used by the EPUB reader)."""
    reader = _HtmlReader(DecodeBudget(limits))
    deadline = Deadline(limits.max_seconds)
    for i in range(0, len(text), FEED_CHUNK):
        deadline.check()
        reader.feed(text[i:i + FEED_CHUNK])
    reader.close()
    while reader.tables:
        reader._cell_text()
        reader._close_table()
    reader.flush()
    return reader.blocks, reader.title, reader.notes


# ---------------------------------------------------------------------------------------------------------------
# RTF

_RTF_TOKEN = re.compile(r"\\([a-zA-Z]{1,32})(-?\d{1,10})? ?|\\'([0-9a-fA-F]{2})|\\([^a-zA-Z])|([{}])|([^\\{}\r\n]+)|[\r\n]+")
_RTF_SKIP = {"fonttbl", "colortbl", "stylesheet", "pict", "object", "header", "footer", "headerl", "headerr",
             "headerf", "footerl", "footerr", "footerf", "listtable", "listoverridetable", "rsidtbl", "generator",
             "xmlnsdecl", "themedata", "colorschememapping", "latentstyles", "datastore", "fldinst", "filetbl",
             "revtbl", "pgdsctbl", "operator", "author", "company", "comment", "keywords", "subject", "doccomm",
             "bkmkstart", "bkmkend", "shppict", "nonshppict", "blipuid", "mmathPr", "xe", "tc", "pn", "pntext",
             "pntxta", "pntxtb", "falt", "panose", "leveltext", "levelnumbers"}


def read_rtf(data, detected, limits):
    deadline, budget = Deadline(limits.max_seconds), DecodeBudget(limits)
    text = data.decode("latin-1")
    if not text.startswith("{\\rtf"):
        raise Malformed("the text does not start with '{\\rtf'")
    codepage = "cp1252"
    m = re.search(r"\\ansicpg(\d{3,5})", text[:4096])
    if m:
        try:
            "".encode("cp" + m.group(1))
            codepage = "cp" + m.group(1)
        except LookupError:
            pass
    stack = [{"skip": False, "uc": 1, "dest": None}]
    paragraphs: list[str] = []
    current: list[str] = []
    title: list[str] = []
    pending_skip = 0
    star = False
    cells = 0
    partial = False
    blocks: list[Block] = []
    pos, n = 0, len(text)
    count = 0
    while pos < n:
        count += 1
        if count % 4096 == 0:
            deadline.check()
        tok = _RTF_TOKEN.match(text, pos)
        if tok is None:
            pos += 1
            continue
        pos = tok.end()
        word, arg, hexa, sym, brace, plain = tok.groups()
        state = stack[-1]
        if brace == "{":
            if len(stack) >= MAX_RTF_DEPTH:
                raise Refused(f"RTF groups nest deeper than {MAX_RTF_DEPTH}", rule="max_depth")
            stack.append(dict(state, dest=None))
            star = False
            continue
        if brace == "}":
            if len(stack) > 1:
                stack.pop()
            continue
        out = None
        if word:
            if word == "bin" and arg:
                pos += max(0, int(arg))
                continue
            if star or word in _RTF_SKIP:
                state["skip"] = True
                star = False
                continue
            if word in ("info",):
                state["skip"] = True
                state["dest"] = "info"
                continue
            if word == "title" and any(s.get("dest") == "info" for s in stack):
                state["skip"], state["dest"] = False, "title"
                continue
            if word == "uc" and arg:
                state["uc"] = max(0, min(int(arg), 8))
                continue
            if word == "u" and arg:
                value = int(arg)
                value = value + 65536 if value < 0 else value
                out = chr(value) if value < 0x110000 else "\ufffd"
                pending_skip = state["uc"]
            elif word in ("par", "line"):
                out = "\n"
            elif word in ("page", "sect"):
                out = "\f"
            elif word == "tab":
                out = "\t"
            elif word == "cell":
                out, cells = "\t", cells + 1
            elif word == "row":
                out = "\n"
            elif word in ("emdash", "endash"):
                out = "—" if word == "emdash" else "–"
            elif word in ("lquote", "rquote"):
                out = "'"
            elif word in ("ldblquote", "rdblquote"):
                out = '"'
            elif word == "bullet":
                out = "•"
            else:
                continue
        elif hexa:
            if pending_skip:
                pending_skip -= 1
                continue
            out = bytes([int(hexa, 16)]).decode(codepage, "replace")
        elif sym:
            if sym == "*":
                star = True
                continue
            out = {"~": "\u00a0", "_": "-", "-": "", "\\": "\\", "{": "{", "}": "}"}.get(sym, "")
            if sym in "\r\n":
                out = "\n"
        elif plain:
            if pending_skip:
                skip = min(pending_skip, len(plain))
                plain, pending_skip = plain[skip:], pending_skip - skip
            out = plain
        else:
            continue
        if state["skip"] and state.get("dest") != "title":
            continue
        if state.get("dest") == "title":
            title.append(out)
            continue
        for ch_part in re.split(r"([\n\f])", out):
            if ch_part == "\n":
                paragraphs.append("".join(current))
                current = []
            elif ch_part == "\f":
                paragraphs.append("".join(current))
                current = []
                paragraphs.append("\f")
            else:
                current.append(ch_part)
    paragraphs.append("".join(current))
    for p in paragraphs:
        p = p.encode("utf-16", "surrogatepass").decode("utf-16", "replace")    # pairs of \\u surrogates
        if p == "\f":
            blocks.append(Block("page_break", 0, ""))
            continue
        p = p.strip()
        if not p:
            continue
        if not budget.fits_elements(1):
            partial = True
            break
        budget.take_elements(1)
        blocks.append(Block("paragraph", 0, p))
    while blocks and blocks[-1].kind == "page_break":
        blocks.pop()
    notes = ["RTF: text and paragraph structure only; no headings, styles, lists, images or embedded objects"]
    if cells:
        notes.append("RTF tables are kept as lines with cells separated by TAB, not as tables")
    if partial:
        notes.append(f"stopped after {len(blocks)} paragraphs: the element limit was reached")
    return _finish(detected, blocks, "".join(title).strip() or None, notes, partial)


# ---------------------------------------------------------------------------------------------------------------
# Adapters


class MarkdownAdapter(Adapter):
    name = "b1-markdown"
    formats = ("markdown", "md")
    families = ("document",)

    def decode(self, data, detected, limits):
        return read_markdown(data, detected, limits)


class HtmlAdapter(Adapter):
    name = "b1-html"
    formats = ("html", "xhtml")
    families = ("document",)

    def decode(self, data, detected, limits):
        return read_html(data, detected, limits)


class RtfAdapter(Adapter):
    name = "b1-rtf"
    formats = ("rtf",)
    families = ("document",)

    def decode(self, data, detected, limits):
        return read_rtf(data, detected, limits)


ADAPTERS = (MarkdownAdapter, HtmlAdapter, RtfAdapter)

__all__ = ["ADAPTERS", "HtmlAdapter", "MarkdownAdapter", "RtfAdapter", "html_blocks", "markdown_blocks",
           "read_html", "read_markdown", "read_rtf"]
