"""Lane G1's readers against hostile bytes: every failure is a named finding, bounded before it is expensive."""
import pytest

import g1_support as S
from neuroscifi.artifacts.check import datafiles, documents, svg_read
from neuroscifi.artifacts.types import ArtifactError

SVG_HEAD = b'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10">'


@pytest.mark.parametrize("data,reason", [
    (b'<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol"><!ENTITY lol2 "&lol;&lol;">]>'
     + SVG_HEAD + b"<text>&lol2;</text></svg>", "refused"),
    (SVG_HEAD + b"<g>" * 100 + b"</g>" * 100 + b"</svg>", "refused"),
    (SVG_HEAD + b"<rect/>" * 300_000 + b"</svg>", "refused"),
    (b"\x89PNG\r\n\x1a\n" + b"\x00" * 64, "malformed"),
    (SVG_HEAD + b"<rect>", "malformed"),
    (b"", "malformed"),
])
def test_hostile_svg_is_refused_with_a_reason(data, reason):
    with pytest.raises(ArtifactError) as e:
        svg_read.parse(data)
    assert e.value.reason == reason
    v = S.verdict(data, None)
    assert not v.ok and "svg_parse" in S.failed(v)


def test_an_oversized_svg_is_refused_before_parsing():
    with pytest.raises(ArtifactError) as e:
        svg_read.parse(b" " * (svg_read.MAX_BYTES + 1))
    assert e.value.reason == "too_large"


def test_svg_in_another_namespace_or_with_xlink_is_not_ok():
    data = (b'<svg xmlns="http://www.w3.org/2000/svg" xmlns:x="http://www.w3.org/1999/xlink"><title>t</title>'
            b'<desc>d</desc><rect x:href="javascript:alert(1)"/><foreignObject><div xmlns="http://www.w3.org/1999/'
            b'xhtml">x</div></foreignObject></svg>')
    failed = S.failed(S.verdict(data, None))
    assert {"svg_no_href", "svg_allowed_elements", "svg_no_foreign_namespace"} <= failed


def test_css_that_loads_or_runs_is_refused():
    data = (SVG_HEAD + b"<title>t</title><desc>d</desc><style>@import url(https://example.invalid/x.css);"
            b"svg{background:url(https://example.invalid/p.png)}</style><rect width=\"1\" height=\"1\"/></svg>")
    failed = S.failed(S.verdict(data, None))
    assert {"svg_css_safe", "svg_no_external_reference"} <= failed


def test_hostile_html_is_bounded():
    with pytest.raises(ArtifactError):
        documents.parse_html(b"<div>" * 300 + b"x")
    with pytest.raises(ArtifactError):
        documents.parse_html(b"\xff\xfe<html>")
    v = S.verdict(b"<html><body><?php evil() ?></body></html>", None, "text/html")
    assert not v.ok


def test_hostile_csv_and_json_are_named_failures():
    with pytest.raises(ArtifactError):
        datafiles.read_csv('a,"never closed\r\n')
    v = S.verdict(b'a,b\r\n"x"y,2\r\n', None, "text/csv")
    assert not v.ok and "csv_dialect" in S.failed(v)
    v = S.verdict(b'{"a": 1, "a": 2}', None, "application/json")
    assert not v.ok and "json_parses" in S.failed(v)
    v = S.verdict(b"[" * 100_000 + b"]" * 100_000, None, "application/json")
    assert not v.ok and "json_parses" in S.failed(v)
