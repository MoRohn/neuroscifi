"""Lane B1: what the planner is handed (ContentShape of decoded artifacts, through K's single rule) and B1's
detection refinement (signatures, structure, hints that lose, prefixes that never raise)."""
import os

import pytest

from neuroscifi.artifacts import detect_b1, shapes_b1

import _b1_support
from _b1_support import make_zip

b1reg = _b1_support.b1reg      # the fixture


def test_shapes_named_by_the_brief(b1reg):
    dated = b1reg.decode(b"day,value\n2024-01-01,3\n2024-01-02,4\n", name="d.csv").representation.shape()
    assert "series" in dated.kinds and dated.has_time and dated.n_series == 1
    cats = b1reg.decode(b"fruit,count\napple,3\npear,5\n", name="c.csv").representation.shape()
    assert "categories" in cats.kinds and not cats.has_time
    calls = b1reg.decode(b"def a():\n    b()\n\ndef b():\n    pass\n", name="m.py").representation.shape()
    assert calls.kinds == ("network",)
    outline = b1reg.decode(b"# A\n\n## B\n\ntext\n", name="o.md").representation.shape()
    assert "hierarchy" in outline.kinds and "prose" in outline.kinds


def test_content_shapes_of_a_bundle_reach_its_members(b1reg):
    data = make_zip([("t.csv", b"day,value\n2024-01-01,3\n2024-01-02,4\n"), ("n.md", b"# T\n\ntext\n")])
    shapes = shapes_b1.content_shapes(b1reg.decode(data, name="a.zip").representation)
    assert shapes[0].kinds == ("hierarchy",)
    assert any("series" in s.kinds for s in shapes[1:]) and any("prose" in s.kinds for s in shapes[1:])
    assert len(shapes_b1.content_shapes(b1reg.decode(data, name="a.zip").representation, limit=2)) == 2


SAMPLES = {
    "pdf": b"%PDF-1.7\n%\xe2\xe3\n1 0 obj\n<<>>\nendobj\n",
    "rtf": b"{\\rtf1\\ansi hello}",
    "sqlite": b"SQLite format 3\x00" + b"\x10\x00\x01\x01" + b"\x00" * 80,
    "7z": b"7z\xbc\xaf\x27\x1c\x00\x04" + b"\x00" * 24,
    "rar": b"Rar!\x1a\x07\x01\x00" + b"\x00" * 24,
    "tar": b"a.txt".ljust(257, b"\x00") + b"ustar\x0000" + b"\x00" * 250,
    "csv": b"a,b,c\n1,2,3\n4,5,6\n",
    "tsv": b"a\tb\n1\t2\n3\t4\n",
    "ndjson": b'{"a": 1}\n{"a": 2}\n{"a": 3}\n',
    "html": b"<!doctype html><html><body><p>x</p></body></html>",
    "xml": b'<?xml version="1.0"?><root><a/></root>',
    "plist": b'<?xml version="1.0"?><plist version="1.0"><dict/></plist>',
    "ipynb": b'{"cells": [], "nbformat": 4, "nbformat_minor": 5, "metadata": {}}',
    "markdown": b"# Title\n\n- item\n- [link](http://x)\n",
    "python": b"#!/usr/bin/env python3\nprint(1)\n",
    "shell": b"#!/bin/bash\necho hi\n",
}


@pytest.mark.parametrize("fmt", sorted(SAMPLES))
def test_refine_identifies_by_bytes(fmt):
    found = detect_b1.detect(SAMPLES[fmt])
    assert found.format == fmt and found.basis in ("signature", "structure"), found


def test_zip_kinds_by_member_names():
    docx = make_zip([("[Content_Types].xml", b"<Types/>"), ("word/document.xml", b"<w/>")])
    assert detect_b1.detect(docx).format == "docx"
    odt = make_zip([("mimetype", b"application/vnd.oasis.opendocument.text")], method=0)
    assert detect_b1.detect(odt).format == "odt"
    assert detect_b1.detect(make_zip([("a.txt", b"x")])).format == "zip"


def test_hints_lose_to_bytes_and_alone_are_declared():
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
    assert detect_b1.detect(png, name="table.csv").format == "png"           # K's signature stands
    html = detect_b1.detect(SAMPLES["html"], name="data.csv")
    assert html.format == "html" and any("structure wins" in r for r in html.reasons)
    js = detect_b1.detect(b"let x = 1;\n", name="a.js")
    assert js.format == "javascript" and js.basis == "declared"
    assert detect_b1.detect(b"[tool]\nx = 1\n", name="pyproject.toml").format == "pyproject"
    assert detect_b1.detect(b"numpy==2\n", name="requirements.txt").format == "requirements"


def test_random_and_truncated_prefixes_never_raise_or_misidentify():
    rng = __import__("random").Random(7)
    wrong = 0
    for _ in range(300):
        blob = bytes(rng.getrandbits(8) for _ in range(rng.randint(0, 600)))
        found = detect_b1.detect(blob)
        wrong += found.format not in ("unknown", "text")
    assert wrong == 0
    for sample in list(SAMPLES.values()) + [make_zip([("word/x", b"")])]:
        for cut in (0, 1, 4, 16):
            detect_b1.detect(sample[:cut])
    assert detect_b1.detect(os.urandom(64)).format in ("unknown", "text")
