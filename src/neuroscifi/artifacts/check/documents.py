"""Independent checks of made HTML (report documents and table fragments) and Markdown documents.

Written from HTML, Markdown and the document contract in ADR-074, never from a generator:
- safety: an allow-list of HTML elements and attributes; no script, no event attribute, no link that leaves the
  document (`href` must be "#id" naming an element of the same document), CSS without url(), @import or
  expressions; every embedded SVG passes the SVG checks;
- structure: a <title> and one <h1>; heading levels that never skip; no empty section; no placeholder text
  (TODO, TBD, FIXME, XXX, lorem ipsum); every figure has a caption; every reference resolves; the made-by-the-system
  label ("Made by NeuroSciFi" and "not evidence", in an element of class "made-by"; in Markdown a quoted line);
- tables: a caption, column headers with scope, row headers with scope when present, and with the source data
  every cell compared (numbers exactly, or to the stated decimals; times as ISO 8601; booleans as yes/no; a missing
  value as a cell of class "missing").
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from html.parser import HTMLParser

from ..types import ArtifactError, Malformed, Refused
from . import Finding

MAX_BYTES = 32 << 20
MAX_NODES = 500_000
MAX_DEPTH = 256
VOID = {"meta", "br", "hr", "img", "input", "link", "area", "base", "col", "embed", "source", "track", "wbr"}
HTML_ELEMENTS = {"html", "head", "body", "meta", "title", "style", "header", "footer", "main", "nav", "section",
                 "article", "aside", "h1", "h2", "h3", "h4", "h5", "h6", "p", "ul", "ol", "li", "table", "caption",
                 "thead", "tbody", "tfoot", "tr", "th", "td", "figure", "figcaption", "pre", "code", "em", "strong",
                 "a", "hr", "span", "div", "small", "br", "blockquote", "dl", "dt", "dd", "sup", "sub", "svg"}
HTML_ATTRIBUTES = {"id", "class", "lang", "charset", "name", "content", "http-equiv", "scope", "colspan", "rowspan",
                   "href", "role", "dir", "title"}
PLACEHOLDER_CASE = re.compile(r"\b(?:TODO|TBD|FIXME|XXX)\b|(?i:lorem ipsum)|(?i:\[placeholder\])")
LABEL_WORDS = ("made by neuroscifi", "not evidence")
_CSS_BAD = re.compile(r"url\s*\(|@import|@font-face|expression\s*\(|javascript\s*:|behavior\s*:|-moz-binding", re.I)


class Node:
    __slots__ = ("tag", "attrs", "children", "parent", "text", "start", "end")

    def __init__(self, tag, attrs, parent, start):
        self.tag, self.attrs, self.parent, self.start = tag, attrs, parent, start
        self.children: list = []          # Node or str
        self.text = ""
        self.end = start

    def iter(self):
        stack = [self]
        while stack:
            n = stack.pop()
            yield n
            stack.extend(reversed([c for c in n.children if isinstance(c, Node)]))

    def all_text(self) -> str:
        out = []
        for c in self.children:
            out.append(c if isinstance(c, str) else c.all_text())
        return "".join(out)

    def classes(self):
        return set((self.attrs.get("class") or "").split())


class _Builder(HTMLParser):
    def __init__(self, text: str):
        super().__init__(convert_charrefs=True)
        self.source = text
        self.line_starts = [0]
        for m in re.finditer("\n", text):
            self.line_starts.append(m.end())
        self.root = Node("#document", {}, None, 0)
        self.cur = self.root
        self.n = 0
        self.depth = 0
        self.doctype = None
        self.comments = 0

    def _offset(self):
        line, col = self.getpos()
        return self.line_starts[line - 1] + col

    def handle_starttag(self, tag, attrs):
        self.n += 1
        if self.n > MAX_NODES:
            raise Refused(f"more than {MAX_NODES} elements", rule="max_elements")
        node = Node(tag, {k: (v if v is not None else "") for k, v in attrs}, self.cur, self._offset())
        self.cur.children.append(node)
        if tag in VOID:
            node.end = self._offset() + len(self.get_starttag_text() or "")
            return
        self.cur = node
        self.depth += 1
        if self.depth > MAX_DEPTH:
            raise Refused(f"elements nest deeper than {MAX_DEPTH}", rule="max_depth")

    def handle_startendtag(self, tag, attrs):
        self.n += 1
        node = Node(tag, {k: (v if v is not None else "") for k, v in attrs}, self.cur, self._offset())
        node.end = node.start + len(self.get_starttag_text() or "")
        self.cur.children.append(node)

    def handle_endtag(self, tag):
        node = self.cur
        while node is not None and node.tag != tag:
            node = node.parent
        if node is None or node is self.root:
            return
        end = self.source.find(">", self._offset())
        node.end = end + 1 if end >= 0 else len(self.source)
        while self.cur is not node:
            self.cur = self.cur.parent
            self.depth -= 1
        self.cur = node.parent
        self.depth -= 1

    def handle_data(self, data):
        self.cur.children.append(data)

    def handle_decl(self, decl):
        self.doctype = decl

    def handle_comment(self, data):
        self.comments += 1

    def handle_pi(self, data):
        raise Refused("a processing instruction is not allowed", rule="processing_instruction")

    def unknown_decl(self, data):
        raise Refused("an unknown declaration is not allowed", rule="xml_dtd")


def parse_html(data: bytes) -> tuple[Node, str, _Builder]:
    if len(data) > MAX_BYTES:
        raise Refused(f"the document is over {MAX_BYTES} bytes", rule="max_bytes", reason="too_large")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise Malformed("the document is not UTF-8") from None
    b = _Builder(text)
    b.feed(text)
    b.close()
    return b.root, text, b


# --- shared pieces ----------------------------------------------------------------------------------------------

def _svg_findings(fragments: list[str]) -> list[Finding]:
    """Each embedded SVG through the SVG checks (safety, title and description)."""
    from .layout import check_layout
    from .svg_read import parse
    from .svg_safety import check_svg
    bad = []
    for i, frag in enumerate(fragments):
        try:
            parsed = parse(frag.encode("utf-8"))
        except ArtifactError as error:
            bad.append(f"figure {i + 1}: {error.reason}")
            continue
        failed = [f.rule for f in check_svg(parsed) if not f.passed]
        tech, _style = check_layout(parsed.root)
        failed += [f.rule for f in tech if not f.passed]
        if failed:
            bad.append(f"figure {i + 1}: {', '.join(sorted(set(failed))[:4])}")
    return [Finding("figures_safe", not bad, f"{len(fragments)} embedded picture(s) pass the SVG checks" if not bad
                    else "; ".join(bad[:4]), float(len(bad)))]


def _placeholders(text: str) -> list[str]:
    return [m.group(0) for m in PLACEHOLDER_CASE.finditer(text)]


def _label_ok(text: str) -> bool:
    low = " ".join(text.lower().split())
    return all(w in low for w in LABEL_WORDS)


def _heading_findings(levels: list[int], sections_empty: int) -> list[Finding]:
    skips = sum(1 for a, b in zip([0] + levels, levels) if b > a + 1)
    first_ok = bool(levels) and levels[0] == 1 and levels.count(1) == 1
    return [Finding("heading_levels", skips == 0 and first_ok,
                    "one h1, and heading levels never skip" if skips == 0 and first_ok else
                    f"{skips} skipped heading level(s){'' if first_ok else '; not exactly one top heading first'}",
                    float(skips)),
            Finding("no_empty_sections", sections_empty == 0, "every section has content" if not sections_empty
                    else f"{sections_empty} empty section(s)", float(sections_empty))]


# --- tables -----------------------------------------------------------------------------------------------------

def _iso(text: str):
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})(?:T(\d{2}):(\d{2}):(\d{2})(\.\d+)?Z)?", text)
    if not m:
        return None
    y, mo, d, h, mi, s, frac = m.groups()
    try:
        dt = datetime(int(y), int(mo), int(d), int(h or 0), int(mi or 0), int(s or 0), tzinfo=timezone.utc)
    except ValueError:
        return None
    delta = dt - datetime(1970, 1, 1, tzinfo=timezone.utc)
    from decimal import Decimal
    return float(Decimal(delta.days * 86400 + delta.seconds) + (Decimal(frac) if frac else 0))


def _cell_matches(kind: str, text: str, value, decimals) -> bool:
    if kind == "number":
        try:
            got = float(text)
        except ValueError:
            return False
        if decimals is not None:
            return abs(got - float(value)) <= 0.5 * 10 ** -decimals + 1e-12 * abs(float(value))
        return got == float(value)
    if kind == "integer":
        return re.fullmatch(r"-?\d+", text) is not None and int(text) == int(value)
    if kind == "boolean":
        return text == ("yes" if value else "no")
    if kind == "time":
        got = _iso(text)
        return got is not None and got == float(value)
    return text == str(value)


def table_findings(table: Node, source: dict | None, options: dict) -> list[Finding]:
    out = []
    caption = next((n for n in table.iter() if n.tag == "caption"), None)
    head_cells = [n for n in table.iter() if n.tag == "th" and n.parent is not None and
                  any(a.tag == "thead" for a in _ancestors(n))]
    scoped = all(n.attrs.get("scope") == "col" for n in head_cells)
    out.append(Finding("table_structure", caption is not None and bool(head_cells) and scoped,
                       "caption and scoped column headers present" if caption is not None and head_cells and scoped
                       else "the table lacks a caption or scoped column headers"))
    if source is None:
        return out
    body_rows = [n for n in table.iter() if n.tag == "tr" and any(a.tag == "tbody" for a in _ancestors(n))]
    rows = [[(c.all_text().strip(), "missing" in c.classes()) for c in r.children
             if isinstance(c, Node) and c.tag in ("td", "th")] for r in body_rows]
    return out + compare_cells([" ".join(n.all_text().split()) for n in head_cells], rows, source,
                               options.get("decimals"))


def compare_cells(heads: list[str], rows: list[list[tuple[str, bool]]], source: dict, decimals) -> list[Finding]:
    """Headers and every cell against the source columns. A cell is (text, marked_missing)."""
    cols = source["columns"]
    want_heads = [" ".join((f"{c['name']} ({c['unit']})" if c.get("unit") else str(c["name"])).split())
                  for c in cols]
    same = heads == want_heads
    out = [Finding("headers_match", same, "every column header names its column and unit" if same else
                   "the column headers differ from the source")]
    n_rows = len(cols[0]["values"]) if cols else 0
    good = total = 0
    for r in range(n_rows):
        cells = rows[r] if r < len(rows) else []
        for i, col in enumerate(cols):
            total += 1
            if i >= len(cells):
                continue
            text, missing = cells[i]
            value = col["values"][r]
            if value is None:
                good += missing
            elif not missing and _cell_matches(col["kind"], text, value, decimals):
                good += 1
    extra_rows = max(0, len(rows) - n_rows)
    out.append(Finding("cells_match", good == total and extra_rows == 0,
                       f"{good} of {total} cells match the source" + (f"; {extra_rows} extra row(s)" if extra_rows
                                                                      else ""), good / total if total else 1.0))
    return out


def _ancestors(n: Node):
    p = n.parent
    while p is not None:
        yield p
        p = p.parent


# --- HTML -------------------------------------------------------------------------------------------------------

def check_html(data: bytes, form: str | None, source: dict | None, options: dict) -> tuple[list, list]:
    root, text, builder = parse_html(data)
    technical: list[Finding] = []
    style: list[Finding] = []
    bad_el, bad_attr, events, scripts, outside_links = set(), set(), 0, 0, 0
    css_bad = set()
    svgs: list[str] = []
    ids = {n.attrs["id"] for n in root.iter() if "id" in n.attrs}
    unresolved = 0
    for n in root.iter():
        if n is root:
            continue
        in_svg = any(a.tag == "svg" for a in _ancestors(n))
        if n.tag == "svg" and not in_svg:
            svgs.append(text[n.start:n.end])
            continue
        if in_svg:
            continue
        if n.tag == "script":
            scripts += 1
        if n.tag not in HTML_ELEMENTS:
            bad_el.add(n.tag)
        for k, v in n.attrs.items():
            if k.startswith("on"):
                events += 1
            elif k not in HTML_ATTRIBUTES and not k.startswith(("aria-", "data-")):
                bad_attr.add(k)
            if k == "href":
                if not v.startswith("#"):
                    outside_links += 1
                elif v[1:] not in ids:
                    unresolved += 1
            if k == "http-equiv" and v.lower() != "content-security-policy":
                bad_attr.add(f"http-equiv={v[:20]}")
            if k == "style":
                bad_attr.add("style")
        if n.tag == "style":
            css_bad |= {m.group(0).lower() for m in _CSS_BAD.finditer(n.all_text())}
    technical += [
        Finding("html_allowed_elements", not bad_el, "every element is on the allow-list" if not bad_el else
                f"elements not allowed: {', '.join(sorted(bad_el)[:6])}"),
        Finding("html_no_script", scripts == 0, "no script" if not scripts else f"{scripts} script element(s)"),
        Finding("html_allowed_attributes", not bad_attr and events == 0,
                "every attribute is on the allow-list" if not bad_attr and not events else
                f"attributes not allowed: {', '.join(sorted(bad_attr)[:6])}" + (f"; {events} event attribute(s)"
                                                                                 if events else "")),
        Finding("links_text_only", outside_links == 0, "no link leaves the document" if not outside_links else
                f"{outside_links} link(s) leave the document"),
        Finding("references_resolve", unresolved == 0, "every reference names an element of the document"
                if not unresolved else f"{unresolved} reference(s) name nothing"),
        Finding("css_safe", not css_bad, "the CSS loads nothing" if not css_bad else
                f"CSS not allowed: {', '.join(sorted(css_bad))}"),
    ]
    if svgs:
        technical += _svg_findings(svgs)
    tables = [n for n in root.iter() if n.tag == "table" and not any(a.tag == "svg" for a in _ancestors(n))]
    is_document = any(n.tag == "html" for n in root.iter()) or form == "document_html"
    if form == "table_html" or (not is_document and tables):
        if not tables:
            technical.append(Finding("table_structure", False, "no table found"))
        for t in tables[:1]:
            technical += table_findings(t, source, options)
        return technical, style
    # A whole document.
    title = next((n for n in root.iter() if n.tag == "title"), None)
    html = next((n for n in root.iter() if n.tag == "html"), None)
    technical.append(Finding("title_present", title is not None and bool(title.all_text().strip()),
                             "the document has a title" if title is not None and title.all_text().strip() else
                             "the document has no <title>"))
    if html is None or not html.attrs.get("lang"):
        style.append(Finding("language_stated", False, "no lang attribute: screen readers guess the language"))
    levels, empties = _html_sections(root)
    technical += _heading_findings(levels, empties)
    visible = _visible_text(root)
    found = _placeholders(visible)
    technical.append(Finding("no_placeholders", not found, "no placeholder text" if not found else
                             f"placeholder text: {', '.join(sorted(set(found))[:4])}", float(len(found))))
    labels = [n for n in root.iter() if "made-by" in n.classes() and _label_ok(n.all_text())]
    technical.append(Finding("made_by_label", bool(labels), "the made-by-the-system label is present" if labels else
                             "the label saying the system made this (and that it is not evidence) is missing"))
    figures = [n for n in root.iter() if n.tag == "figure"]
    uncaptioned = sum(1 for f in figures if not any(c.tag == "figcaption" and c.all_text().strip()
                                                    for c in f.iter()))
    technical.append(Finding("figures_captioned", uncaptioned == 0, f"{len(figures)} figure(s), each captioned"
                             if not uncaptioned else f"{uncaptioned} figure(s) without a caption"))
    based_on = any(re.search(r"what this is based on", n.all_text(), re.I) for n in root.iter()
                   if n.tag in ("h2", "h3"))
    technical.append(Finding("sources_section", based_on, "a 'What this is based on' section is present" if based_on
                             else "no section says what the document is based on"))
    for t in tables:
        style.extend(f for f in table_findings(t, None, {}) if not f.passed)
    technical += _document_tables(tables, source, lambda t, data: table_findings(t, data, {}))
    return technical, style


def _document_tables(tables, source, compare) -> list[Finding]:
    """With the document's spec data: the n-th table holds the n-th table block's data, cell for cell."""
    if not isinstance(source, dict) or "blocks" not in source:
        return []
    blocks = [b["table"] for b in source["blocks"] if b.get("kind") == "table" and "table" in b]
    good = total = 0
    heads_ok = len(tables) == len(blocks)
    for t, data in zip(tables, blocks):
        for f in compare(t, data):
            if f.rule == "cells_match":
                n = sum(len(c["values"]) for c in data["columns"])
                total += n
                good += round((f.measure or 0) * n)
            elif f.rule == "headers_match" and not f.passed:
                heads_ok = False
    total += sum(sum(len(c["values"]) for c in data["columns"]) for data in blocks[len(tables):])
    return [Finding("cells_match", good == total and heads_ok,
                    f"{good} of {total} table cells match the specification across {len(blocks)} table(s)"
                    + ("" if heads_ok else "; headers or table count differ"), good / total if total else 1.0)]


def _visible_text(root: Node) -> str:
    out = []
    for n in root.iter():
        if n.tag in ("style", "script", "title", "svg", "metadata"):
            continue
        if any(a.tag in ("style", "script", "svg") for a in _ancestors(n)):
            continue
        out.extend(c for c in n.children if isinstance(c, str))
    return " ".join(out)


def _html_sections(root: Node) -> tuple[list[int], int]:
    """Heading levels in order, and how many headings are followed by no content before the next heading of the
    same or a higher rank (or the end)."""
    seq = []           # ("h", level) or ("c", None) in document order

    def walk(n: Node):
        for c in n.children:
            if isinstance(c, str):
                if c.strip():
                    seq.append(("c", None))
            elif c.tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
                seq.append(("h", int(c.tag[1])))
            elif c.tag in ("style", "script", "title", "head"):
                continue
            elif c.tag in ("table", "figure", "svg", "pre", "ul", "ol", "hr"):
                seq.append(("c", None))
            else:
                walk(c)
    walk(root)
    levels = [lv for kind, lv in seq if kind == "h"]
    empty = 0
    for i, (kind, lv) in enumerate(seq):
        if kind != "h":
            continue
        has = False
        for kind2, lv2 in seq[i + 1:]:
            if kind2 == "c":
                has = True
                break
            if lv2 <= lv:
                break
            has = True          # a subsection is content
            break
        empty += not has
    return levels, empty


# --- Markdown ---------------------------------------------------------------------------------------------------

_LINK = re.compile(r"(?<!\\)\[(?:[^\]\\]|\\.)*(?<!\\)\]\(")
_REF_DEF = re.compile(r"^\s{0,3}(?<!\\)\[[^\]]+\]:\s")
_AUTOLINK = re.compile(r"(?<!\\)<[A-Za-z][A-Za-z0-9+.-]{1,31}:[^\s<>]*>")
_BARE = re.compile(r"(?i)\b(?:https?|ftp)://|\bwww\.[a-z0-9-]+\.")
_RAW_TAG = re.compile(r"(?<!\\)<(?:/?[A-Za-z][A-Za-z0-9-]*|!--|\?)")
_CODE_SPAN = re.compile(r"(`+)(?:(?!\1).)*?\1")


def check_markdown(data: bytes, source: dict | None = None) -> tuple[list, list]:
    if len(data) > MAX_BYTES:
        raise Refused(f"the document is over {MAX_BYTES} bytes", rule="max_bytes", reason="too_large")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise Malformed("the document is not UTF-8") from None
    lines = text.split("\n")
    technical: list[Finding] = []
    seq = []             # ("h", level) / ("c", None)
    svgs, links, raw_html, plain = [], 0, 0, []
    fence = None
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if fence:
            if stripped.startswith(fence):
                fence = None
            i += 1
            continue
        m = re.match(r"^\s{0,3}(`{3,}|~{3,})", line)
        if m:
            fence = m.group(1)
            seq.append(("c", None))
            i += 1
            continue
        if line.startswith("<svg"):
            block = [line]
            i += 1
            while i < len(lines) and lines[i].strip():
                block.append(lines[i])
                i += 1
            svgs.append("\n".join(block))
            seq.append(("c", None))
            continue
        h = re.match(r"^\s{0,3}(#{1,6})\s+(.*)$", line)
        if h:
            seq.append(("h", len(h.group(1))))
            plain.append(h.group(2))
        elif stripped:
            seq.append(("c", None))
            plain.append(line)
        if _REF_DEF.match(line):
            links += 1
        outside = _CODE_SPAN.sub("", line)
        links += len(_LINK.findall(outside)) + len(_AUTOLINK.findall(outside)) + len(_BARE.findall(outside))
        raw_html += len(_RAW_TAG.findall(_AUTOLINK.sub("", outside)))
        i += 1
    levels = [lv for k, lv in seq if k == "h"]
    empty = 0
    for j, (kind, lv) in enumerate(seq):
        if kind == "h":
            nxt = seq[j + 1] if j + 1 < len(seq) else None
            empty += nxt is None or (nxt[0] == "h" and nxt[1] <= lv)
    technical += _heading_findings(levels, empty)
    body = "\n".join(_CODE_SPAN.sub("", p) for p in plain)
    found = _placeholders(body)
    technical.append(Finding("no_placeholders", not found, "no placeholder text" if not found else
                             f"placeholder text: {', '.join(sorted(set(found))[:4])}", float(len(found))))
    label = any(ln.startswith(">") and _label_ok(ln.replace("\\", "")) for ln in lines)
    technical.append(Finding("made_by_label", label, "the made-by-the-system label is present" if label else
                             "the label saying the system made this (and that it is not evidence) is missing"))
    technical.append(Finding("links_text_only", links == 0, "no links (addresses are text)" if not links else
                             f"{links} link(s) found"))
    technical.append(Finding("no_raw_html", raw_html == 0, "no raw HTML outside the embedded pictures"
                             if not raw_html else f"{raw_html} raw HTML tag(s) outside the embedded pictures"))
    based_on = any(re.match(r"^#{1,6}\s+What this is based on", ln) for ln in lines)
    technical.append(Finding("sources_section", based_on, "a 'What this is based on' section is present" if based_on
                             else "no section says what the document is based on"))
    if svgs:
        technical += _svg_findings(svgs)
    technical += _document_tables(_md_tables(lines), source, _md_compare)
    return technical, []


def _md_unescape(text: str) -> str:
    return re.sub(r"\\([!-/:-@\[-`{-~])", r"\1", text)


def _md_split(line: str) -> list[str]:
    cells, cur, i = [], [], 0
    body = line.strip()
    if body.startswith("|"):
        body = body[1:]
    if body.endswith("|") and not body.endswith("\\|"):
        body = body[:-1]
    while i < len(body):
        if body[i] == "\\" and i + 1 < len(body):
            cur.append(body[i:i + 2])
            i += 2
            continue
        if body[i] == "|":
            cells.append("".join(cur))
            cur = []
        else:
            cur.append(body[i])
        i += 1
    cells.append("".join(cur))
    return [_md_unescape(c.strip()) for c in cells]


def _md_tables(lines: list[str]):
    """Pipe tables: (header cells, body rows) for each run of lines starting with '|'."""
    out, run = [], []
    for line in lines + [""]:
        if line.startswith("|"):
            run.append(line)
            continue
        if len(run) >= 2 and re.fullmatch(r"\|(?:\s*:?-{3,}:?\s*\|)+", run[1].strip()):
            out.append((_md_split(run[0]), [_md_split(r) for r in run[2:]]))
        run = []
    return out


def _md_compare(table, data):
    heads, rows = table
    return compare_cells([" ".join(h.split()) for h in heads],
                         [[(c, c == "–") for c in r] for r in rows], data, None)


__all__ = ["check_html", "check_markdown", "parse_html", "table_findings"]
