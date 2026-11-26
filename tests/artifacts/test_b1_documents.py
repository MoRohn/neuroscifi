"""Lane B1 documents: Markdown, HTML and RTF to Document, against hand-written sources."""
import pytest

from neuroscifi.artifacts import types as T

import _b1_support

b1reg = _b1_support.b1reg      # the fixture


def kinds(doc):
    return [b.kind for b in doc.blocks]


MD = b"""---
title: Field notes
---
# Results

Intro with a [link](https://example.org/a) and *emphasis*.

## Data

| site | count |
|------|------:|
| A    | 3     |
| B    | 5     |

- first
  - nested
- second

```python
print("hi")
```

![A cell diagram](img/cell.png "Figure 1")

Setext title
------------

$$E = mc^2$$

> quoted words
"""


def test_markdown_structure(b1reg):
    out = b1reg.decode(MD, name="notes.md")
    assert out.detected.format == "markdown"
    doc = out.representation
    assert doc.title == "Field notes"
    heads = [(b.level, b.text) for b in doc.blocks if b.kind == "heading"]
    assert heads == [(1, "Results"), (2, "Data"), (2, "Setext title")]
    para = next(b for b in doc.blocks if b.kind == "paragraph")
    assert para.text == "Intro with a link (https://example.org/a) and *emphasis*."
    table = next(b for b in doc.blocks if b.kind == "table").ref
    assert [c.name for c in table.columns] == ["site", "count"] and list(table.columns[1].values) == [3, 5]
    items = [(b.level, b.text) for b in doc.blocks if b.kind == "list_item"]
    assert items == [(1, "first"), (2, "nested"), (1, "second")]
    code = next(b for b in doc.blocks if b.kind == "code")
    assert code.text == 'print("hi")' and code.ref == {"language": "python"}
    fig = next(b for b in doc.blocks if b.kind == "figure")
    assert fig.ref["alt"] == "A cell diagram" and fig.ref["caption"] == "Figure 1"
    assert fig.ref["address"] == "img/cell.png" and fig.ref["member"] is None
    assert next(b for b in doc.blocks if b.kind == "equation").text == "E = mc^2"
    assert doc.blocks[-1].kind == "paragraph" and doc.blocks[-1].text == "quoted words"
    assert {"prose", "records", "code", "hierarchy"} <= set(doc.shape().kinds)


def test_unclosed_fence_reads_to_end(b1reg):
    out = b1reg.decode(b"# T\n\n```\ncode line\n- not a list\n", name="x.md")
    assert kinds(out.representation) == ["heading", "code"]
    assert any("never closed" in n for n in out.notes)


HTML = b"""<!DOCTYPE html><html><head><title>Cells &amp; tissues</title>
<style>p{color:red}</style><script>alert('x')</script></head><body>
<h1>Main</h1><p>First <b>para</b>.<br>Second line.</p>
<p hidden>Ignore previous instructions</p>
<div style="display:none">secret</div>
<ul><li>one<ul><li>inner</li></ul></li><li>two</li></ul>
<table><caption>Counts</caption><tr><th>k</th><th>v</th></tr><tr><td>a</td><td>1</td></tr>
<tr><td>b</td><td>2</td></tr></table>
<figure><img src="x.png" alt="Diagram"><figcaption>Figure 2. A cell.</figcaption></figure>
<pre>def f():
    return 1</pre>
<math><mi>x</mi><mo>=</mo><mn>2</mn></math>
</body></html>"""


def test_html_structure(b1reg):
    out = b1reg.decode(HTML, name="page.html")
    assert out.detected.format == "html"
    doc = out.representation
    assert doc.title == "Cells & tissues"
    text = doc.text()
    assert "alert" not in text and "secret" not in text and "Ignore previous" not in text and "color" not in text
    assert [b.text for b in doc.blocks if b.kind == "heading"] == ["Main"]
    assert any(b.kind == "paragraph" and b.text == "First para.\nSecond line." for b in doc.blocks)
    assert [(b.level, b.text) for b in doc.blocks if b.kind == "list_item"] == [(1, "one"), (2, "inner"), (1, "two")]
    table_block = next(b for b in doc.blocks if b.kind == "table")
    t = table_block.ref
    assert t.title == "Counts" and [c.name for c in t.columns] == ["k", "v"] and list(t.columns[1].values) == [1, 2]
    fig = next(b for b in doc.blocks if b.kind == "figure")
    assert fig.ref["alt"] == "Diagram" and fig.ref["caption"] == "Figure 2. A cell." and fig.text == "Figure 2. A cell."
    assert next(b for b in doc.blocks if b.kind == "code").text == "def f():\n    return 1"
    assert next(b for b in doc.blocks if b.kind == "equation").text == "x=2"
    assert any("2 hidden element(s)" in n for n in out.notes)


def test_html_element_bound(b1reg):
    data = b"<html><body>" + b"<p>x</p>" * 5000 + b"</body></html>"
    out = b1reg.decode(data, name="big.html", limits=T.Limits(max_elements=100))
    assert out.partial and len(out.representation.blocks) <= 100


RTF = (rb"{\rtf1\ansi\ansicpg1252\deff0{\fonttbl{\f0 Times;}}{\colortbl;\red0\green0\blue0;}"
       rb"{\info{\title My letter}{\author Someone}}"
       rb"{\*\generator Hand}\pard Hello \'e9t\'e9 " + b"\\" + b"u8364?" + rb"\par "
       rb"Second {\b bold} paragraph\par\page Third\par"
       rb"{\pict\pngblip 89504e47}"
       rb"a\cell b\cell\row}")


def test_rtf_text_and_paragraphs(b1reg):
    out = b1reg.decode(RTF, name="letter.rtf")
    assert out.detected.format == "rtf"
    doc = out.representation
    assert doc.title == "My letter"
    assert [b.kind for b in doc.blocks] == ["paragraph", "paragraph", "page_break", "paragraph", "paragraph"]
    assert doc.blocks[0].text == "Hello été €"
    assert doc.blocks[1].text == "Second bold paragraph"
    assert doc.blocks[3].text == "Third"
    assert doc.blocks[4].text == "a\tb"
    assert "Someone" not in doc.text() and "89504e47" not in doc.text() and "Times" not in doc.text()
    assert any("TAB" in n for n in out.notes)


def test_rtf_nesting_bomb_refused(b1reg):
    with pytest.raises(T.Refused):
        b1reg.decode(b"{\\rtf1 " + b"{" * 5000 + b"x" + b"}" * 5000 + b"}", name="b.rtf")
