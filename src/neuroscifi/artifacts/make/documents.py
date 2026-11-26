"""Report documents from blocks (DESIGN §2 `Document`), as one self-contained HTML file or as Markdown.

Both carry: the made-by-the-system label (class "made-by" in HTML; the first line, a quote, in Markdown) at the top
and the bottom; the title and description; a table of contents (headings of levels 1 and 2); the blocks; and a
"What this is based on" section listing the document's sources (addresses as text, never as links) and the
specification's own sources. No script, no external reference: figures are other specifications made here and
embedded inline as SVG; tables are real tables. A reference to a figure or a table is a structured part of a
paragraph ({"ref": id}), never something read out of text, and becomes "Figure n" / "Table n" (a link inside the
HTML document; plain text in Markdown). Heading level 1 is a section (h2: the title is the only h1); levels may not
skip. Equations are shown as their source text, not typeset.
"""
from __future__ import annotations

import re

from .charts import HISTORY, STR
from .scales import Fit
from .svg_base import esc
from .tables import COLUMN, check_table, header_text, cell_text, column_is_dates, table_html

ID = {"type": "string", "pattern": r"^[A-Za-z0-9_-]{1,64}$"}
TEXT = {"type": "string", "maxLength": 4096}
BLOCK_KINDS = ("heading", "paragraph", "list_item", "table", "figure", "code", "equation", "note", "page_break")
BLOCK = {"type": "object", "additionalProperties": False, "required": ["kind"], "properties": {
    "kind": {"enum": list(BLOCK_KINDS)},
    "level": {"type": "integer", "minimum": 1, "maximum": 5},
    "text": TEXT,
    "parts": {"type": "array", "maxItems": 200, "items": {
        "type": "object", "additionalProperties": False,
        "properties": {"text": TEXT, "ref": ID}}},
    "id": ID,
    "caption": STR,
    "table": {"type": "object", "additionalProperties": False, "required": ["columns"],
              "properties": {"columns": {"type": "array", "minItems": 1, "maxItems": 50, "items": COLUMN},
                             "row_header": {"type": "boolean"}}},
    "figure": {"type": "object"}}}
SOURCE = {"type": "object", "additionalProperties": False, "required": ["title"],
          "properties": {"id": ID, "title": STR, "detail": {"type": "string", "maxLength": 1000},
                         "address": {"type": "string", "maxLength": 2000}}}
DATA = {"type": "object", "additionalProperties": False, "required": ["blocks"],
        "properties": {"blocks": {"type": "array", "minItems": 1, "maxItems": 2000, "items": BLOCK},
                       "sources": {"type": "array", "maxItems": 200, "items": SOURCE}}}
OPTIONS = {"type": "object", "additionalProperties": False, "properties": {
    "contents": {"type": "boolean"},
    "lang": {"type": "string", "pattern": r"^[a-z]{2,3}(-[A-Za-z0-9]{2,8})?$"},
    "history": HISTORY}}

LABEL = "Made by NeuroSciFi from a specification. It is not evidence and it is not a source."
MAX_FIGURES = 40

CSS = """:root{--ink:#1c1c1c;--muted:#5a5a5a;--rule:#d6d6d6;--paper:#ffffff;--tint:#f3f3f3;color-scheme:light dark}
@media (prefers-color-scheme:dark){:root{--ink:#e6e6e6;--muted:#a8a8a8;--rule:#3a3a3a;--paper:#161616;--tint:#202020}}
body{margin:0;background:var(--paper);color:var(--ink);font:16px/1.55 system-ui,-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif}
main,header,footer,nav,section.based-on{max-width:46rem;margin:0 auto;padding:0 1.25rem}
header{padding-top:2rem}h1{font-size:1.9rem;line-height:1.2;margin:.4rem 0 .6rem}h2{font-size:1.35rem;margin:2rem 0 .6rem}
h3{font-size:1.12rem;margin:1.5rem 0 .5rem}h4,h5,h6{font-size:1rem;margin:1.2rem 0 .4rem}
.made-by{font-size:.85rem;color:var(--muted);border:1px solid var(--rule);border-radius:6px;padding:.35rem .6rem;display:inline-block}
.description{color:var(--muted)}a{color:inherit}
nav ol{padding-left:1.4rem}figure{margin:1.5rem 0}figure svg{max-width:100%;height:auto}
figcaption,caption{font-size:.9rem;color:var(--muted);text-align:left;padding:.3rem 0}
table{border-collapse:collapse;width:100%;font-size:.92rem;margin:1rem 0}th,td{border-bottom:1px solid var(--rule);padding:.3rem .5rem;text-align:left;vertical-align:top}
thead th{border-bottom:2px solid var(--ink)}td.num{text-align:right;font-variant-numeric:tabular-nums}.unit{color:var(--muted);font-weight:normal}
pre{background:var(--tint);padding:.8rem;overflow-x:auto;border-radius:6px}code{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:.9em}
aside.note{border-left:3px solid var(--rule);padding:.2rem .9rem;color:var(--muted)}hr.page-break{border:0;border-top:1px dashed var(--rule);margin:2rem 0}
footer{padding:2rem 1.25rem 3rem}
@media print{hr.page-break{break-after:page;border:0}.made-by{border-color:#999}}"""
CSP = "default-src 'none'; style-src 'unsafe-inline'; img-src data:"


# --- planning: numbering, references, figures -------------------------------------------------------------------

def _plan(spec):
    """Validate the blocks' structure; number figures and tables; make every figure. Returns (blocks, numbers,
    figures) where numbers maps an id to ("Figure"|"Table", n)."""
    blocks = spec.data["blocks"]
    numbers: dict[str, tuple[str, int]] = {}
    counts = {"figure": 0, "table": 0}
    last = 0
    for b in blocks:
        k = b["kind"]
        if k == "heading":
            level = b.get("level", 1)
            if level > last + 1:
                raise Fit(f"heading levels skip from {last} to {level}")
            last = level
            if not b.get("text", "").strip():
                raise Fit("a heading has no text")
        if k in ("figure", "table"):
            counts[k] += 1
            if b.get("id"):
                if b["id"] in numbers:
                    raise Fit(f"the id {b['id']!r} is used twice")
                numbers[b["id"]] = ("Figure" if k == "figure" else "Table", counts[k])
        if k == "table" and "table" not in b:
            raise Fit("a table block needs its table")
        if k == "figure" and "figure" not in b:
            raise Fit("a figure block needs its figure specification")
        if k in ("paragraph", "list_item", "code", "equation", "note") and not (b.get("text") or b.get("parts")):
            raise Fit(f"a {k} block has no text")
    if counts["figure"] > MAX_FIGURES:
        raise Fit(f"more than {MAX_FIGURES} figures")
    for b in blocks:
        for part in b.get("parts", []):
            if ("ref" in part) == ("text" in part):
                raise Fit("each part of a paragraph is either text or a reference")
            if "ref" in part and part["ref"] not in numbers:
                raise Fit(f"a reference names {part['ref']!r}, which no figure or table carries")
    figures = {}
    from . import make as make_registry
    for i, b in enumerate(blocks):
        if b["kind"] == "figure":
            made = make_registry(b["figure"])
            if made.media_type != "image/svg+xml":
                raise Fit(f"a figure must be a picture made as SVG, not {made.media_type}")
            svg = made.bytes.decode("utf-8")
            if svg.startswith("<?xml"):
                svg = svg[svg.index("?>") + 2:]
            figures[i] = (svg, made.spec.title)
    return blocks, numbers, figures


def _paragraph_text(b, numbers, link: bool, escape) -> str:
    if "parts" not in b:
        return escape(b.get("text", ""))
    out = []
    for part in b["parts"]:
        if "text" in part:
            out.append(escape(part["text"]))
        else:
            word, n = numbers[part["ref"]]
            out.append(f'<a href="#ref-{esc(part["ref"])}">{word} {n}</a>' if link else f"{word} {n}")
    return "".join(out)


# --- HTML -------------------------------------------------------------------------------------------------------

def make_html(spec):
    blocks, numbers, figures = _plan(spec)
    o = spec.options
    lang = o.get("lang", "en")
    body, toc = [], []
    section = 0
    list_depth = 0
    counters = {"Figure": 0, "Table": 0}

    def close_lists(to=0):
        nonlocal list_depth
        while list_depth > to:
            body.append("</li></ul>")
            list_depth -= 1

    for i, b in enumerate(blocks):
        k = b["kind"]
        if k != "list_item":
            close_lists()
        if k == "heading":
            section += 1
            level = b.get("level", 1)
            hid = f"s-{section}"
            body.append(f'<h{level + 1} id="{hid}">{esc(b["text"])}</h{level + 1}>')
            if level <= 2:
                toc.append((level, hid, b["text"]))
        elif k == "paragraph":
            body.append(f"<p>{_paragraph_text(b, numbers, True, esc)}</p>")
        elif k == "list_item":
            level = b.get("level", 1)
            if level > list_depth + 1:
                level = list_depth + 1
            if level > list_depth:
                while list_depth < level:
                    body.append("<ul><li>")
                    list_depth += 1
            else:
                close_lists(level)
                body.append("</li><li>")
            body.append(_paragraph_text(b, numbers, True, esc))
        elif k == "table":
            counters["Table"] += 1
            cap = f"Table {counters['Table']}. {b.get('caption', '')}".strip()
            tid = f"ref-{b['id']}" if b.get("id") else f"table-{counters['Table']}"
            body.append(table_html(cap, "", b["table"], {}, tid))
        elif k == "figure":
            counters["Figure"] += 1
            svg, made_title = figures[i]
            fid = f"ref-{b['id']}" if b.get("id") else f"figure-{counters['Figure']}"
            cap = b.get("caption") or made_title
            body.append(f'<figure id="{fid}">{svg}<figcaption>Figure {counters["Figure"]}. {esc(cap)}</figcaption>'
                        "</figure>")
        elif k == "code":
            body.append(f"<pre><code>{esc(b.get('text', ''))}</code></pre>")
        elif k == "equation":
            body.append(f'<p class="equation"><code>{esc(b.get("text", ""))}</code></p>')
        elif k == "note":
            body.append(f'<aside class="note"><p>{_paragraph_text(b, numbers, True, esc)}</p></aside>')
        elif k == "page_break":
            body.append('<hr class="page-break">')
    close_lists()
    out = ["<!DOCTYPE html>", f'<html lang="{esc(lang)}">', "<head>", '<meta charset="utf-8">',
           f'<meta http-equiv="Content-Security-Policy" content="{esc(CSP)}">',
           '<meta name="viewport" content="width=device-width, initial-scale=1">',
           f"<title>{esc(spec.title)}</title>", f"<style>{CSS}</style>", "</head>", "<body>",
           f'<header><p class="made-by">{esc(LABEL)}</p><h1>{esc(spec.title)}</h1>']
    if spec.description.strip():
        out.append(f'<p class="description">{esc(spec.description)}</p>')
    out.append("</header>")
    if o.get("contents", True) and toc:
        out.append('<nav aria-label="Contents"><h2>Contents</h2><ol>')
        depth = 1
        for n, (level, hid, text) in enumerate(toc):
            if level > depth:
                out.append("<ol>")
            elif level < depth:
                out.append("</li></ol></li>")
            elif n:
                out.append("</li>")
            depth = level
            out.append(f'<li><a href="#{hid}">{esc(text)}</a>')
        out.append("</li>" + "</ol></li>" * (depth - 1) + "</ol></nav>")
    out.append("<main>" + "".join(body) + "</main>")
    out.append('<section class="based-on" id="based-on"><h2>What this is based on</h2>')
    out.extend(_sources_html(spec))
    out.append("</section>")
    out.append(f'<footer><p class="made-by">{esc(LABEL)}</p></footer>')
    out.append("</body></html>")
    return "\n".join(out) + "\n", ()


def _source_words(s) -> list[str]:
    return [p for p in (s.get("title"), s.get("detail"), s.get("address")) if p]


def _sources_html(spec) -> list[str]:
    sources = spec.data.get("sources", [])
    out = []
    if sources:
        out.append("<ol>")
        for s in sources:
            parts = [esc(s["title"])]
            if s.get("detail"):
                parts.append(esc(s["detail"]))
            if s.get("address"):
                parts.append(f"<code>{esc(s['address'])}</code>")
            out.append("<li>" + " — ".join(parts) + "</li>")
        out.append("</ol>")
    else:
        out.append("<p>The document names no sources of its own.</p>")
    made_from = ", ".join("the request" if s == "request" else f"artifact {s}" for s in spec.sources) or "nothing listed"
    out.append(f"<p>The specification was written from: {esc(made_from)}.</p>")
    return out


# --- Markdown ---------------------------------------------------------------------------------------------------

_MD_ALWAYS = re.compile(r"([\\`*_\[\]<>&|~])")


def md_escape(text: str) -> str:
    """Escape Markdown so that text stays text: inline markup characters always; per line, leading white space is
    dropped (it would start a code block) and a line-start marker (# - + > = or "1." / "1)") is escaped."""
    lines = []
    for line in str(text).split("\n"):
        line = _MD_ALWAYS.sub(r"\\\1", line.lstrip(" \t"))
        if line[:1] in ("#", "-", "+", ">", "="):
            line = "\\" + line
        else:
            m = re.match(r"^\d+(?=[.)])", line)
            if m:
                line = m.group(0) + "\\" + line[m.end():]
        lines.append(line)
    return "  \n".join(lines)


def _fence(text: str) -> str:
    longest = max((len(m) for m in re.findall(r"`+", text)), default=0)
    return "`" * max(3, longest + 1)


def _code_span(text: str) -> str:
    longest = max((len(m) for m in re.findall(r"`+", text)), default=0)
    tick = "`" * (longest + 1)
    pad = " " if text.startswith("`") or text.endswith("`") or not text.strip() else ""
    return f"{tick}{pad}{text.replace(chr(10), ' ')}{pad}{tick}"


def _md_table(caption: str, data: dict) -> list[str]:
    n = check_table(data)
    cols = data["columns"]
    dates = [column_is_dates(c) for c in cols]

    def cell(t):    # inside a table row nothing is at a line start: inline escapes only
        return _MD_ALWAYS.sub(r"\\\1", t.replace("\n", " ")) if t else " "
    out = [f"*{md_escape(caption)}*", "", "| " + " | ".join(cell(header_text(c)) for c in cols) + " |",
           "|" + "|".join("---:" if c["kind"] in ("number", "integer") else "---" for c in cols) + "|"]
    for r in range(n):
        texts = [cell_text(c, c["values"][r], None, dates[i]) for i, c in enumerate(cols)]
        out.append("| " + " | ".join("–" if t is None else cell(t) for t in texts) + " |")     # – : missing
    return out


def make_markdown(spec):
    blocks, numbers, figures = _plan(spec)
    out = [f"> {md_escape(LABEL)}", "", f"# {md_escape(spec.title)}", ""]
    if spec.description.strip():
        out += [md_escape(spec.description), ""]
    headings = [b for b in blocks if b["kind"] == "heading" and b.get("level", 1) <= 2]
    if spec.options.get("contents", True) and headings:
        out += ["## Contents", ""]
        n1 = 0
        for b in headings:
            if b.get("level", 1) == 1:
                n1 += 1
                out.append(f"{n1}. {md_escape(b['text'])}")
            else:
                out.append(f"    - {md_escape(b['text'])}")
        out.append("")
    counters = {"Figure": 0, "Table": 0}
    for i, b in enumerate(blocks):
        k = b["kind"]
        if k == "heading":
            out += ["#" * (b.get("level", 1) + 1) + " " + md_escape(b["text"]), ""]
        elif k == "paragraph":
            out += [_paragraph_text(b, numbers, False, md_escape), ""]
        elif k == "list_item":
            indent = "    " * (b.get("level", 1) - 1)
            out.append(f"{indent}- {_paragraph_text(b, numbers, False, md_escape)}")
            if i + 1 >= len(blocks) or blocks[i + 1]["kind"] != "list_item":
                out.append("")
        elif k == "table":
            counters["Table"] += 1
            out += _md_table(f"Table {counters['Table']}. {b.get('caption', '')}".strip(), b["table"]) + [""]
        elif k == "figure":
            counters["Figure"] += 1
            svg, made_title = figures[i]
            svg = svg.replace("\r", " ").replace("\n", " ")    # one HTML block: no blank line may end it early
            head_end = svg.index(">") + 1
            out += [svg[:head_end], svg[head_end:], "",
                    f"*Figure {counters['Figure']}. {md_escape(b.get('caption') or made_title)}*", ""]
        elif k == "code":
            fence = _fence(b.get("text", ""))
            out += [fence, b.get("text", ""), fence, ""]
        elif k == "equation":
            out += [_code_span(b.get("text", "")), ""]
        elif k == "note":
            out += ["> " + _paragraph_text(b, numbers, False, md_escape).replace("\n", "\n> "), ""]
        elif k == "page_break":
            out += ["---", ""]
    out += ["## What this is based on", ""]
    sources = spec.data.get("sources", [])
    for n, s in enumerate(sources, 1):
        parts = [md_escape(s["title"])] + ([md_escape(s["detail"])] if s.get("detail") else []) + \
                ([_code_span(s["address"])] if s.get("address") else [])
        out.append(f"{n}. " + " — ".join(parts))
    if not sources:
        out.append("The document names no sources of its own.")
    made_from = ", ".join("the request" if s == "request" else f"artifact {s}" for s in spec.sources) or "nothing listed"
    out += ["", f"The specification was written from: {md_escape(made_from)}.", "", f"> {md_escape(LABEL)}"]
    return "\n".join(out) + "\n", ()


__all__ = ["DATA", "LABEL", "OPTIONS", "make_html", "make_markdown", "md_escape"]
