"""Lane B1: hostile inputs found while measuring (scripts/omni_b1_evidence.py): patterns that would take
quadratic time, depth refusals named as such, capped ODF repeats marked partial, memory charged for XML trees."""
import time

import pytest

from neuroscifi.artifacts.detect import detect
from neuroscifi.artifacts import types as T

import _b1_support

b1reg = _b1_support.b1reg      # the fixture


@pytest.mark.parametrize("payload", [b"[" * 60_000, b"# " + b" " * 200_000 + b"x", b"![" * 50_000,
                                     b"[a](" * 50_000])
def test_markdown_and_detection_stay_linear(b1reg, payload):
    start = time.perf_counter()
    detect(payload[:65536], name="x.md")
    b1reg.decode(payload, name="x.md")
    assert time.perf_counter() - start < 2.0


def test_long_c_lines_are_not_pattern_matched(b1reg):
    start = time.perf_counter()
    out = b1reg.decode(b"int " + b"a " * 100_000 + b"(\n", name="m.c")
    assert time.perf_counter() - start < 2.0 and not [n for n in out.representation.nodes if n.kind == "function"]


def test_xml_depth_refusal_names_its_rule(b1reg):
    with pytest.raises(T.Refused) as err:
        b1reg.decode(b"<x>" * 1000 + b"</x>" * 1000, name="deep.xml")
    assert err.value.rule == "xml_depth"            # lane K's parse_xml names it


def test_xml_tree_memory_is_charged(b1reg):
    data = b"<r>" + b"<i/>" * 20_000 + b"</r>"
    with pytest.raises(T.Refused):
        b1reg.decode(data, name="w.xml", limits=T.Limits(max_decoded_bytes=1 << 20))
    assert b1reg.decode(data, name="w.xml").representation.root.name == "r"


def test_capped_ods_repeats_mark_partial(b1reg):
    from test_b1_office import ODF, odf
    content = (f'<office:document-content {ODF}><office:body><office:spreadsheet><table:table table:name="T">'
               '<table:table-row table:number-rows-repeated="100000"><table:table-cell office:value-type="float" '
               'office:value="1"/></table:table-row></table:table></office:spreadsheet></office:body>'
               '</office:document-content>')
    out = b1reg.decode(odf("application/vnd.oasis.opendocument.spreadsheet", content), name="r.ods")
    assert out.partial and out.representation.members[0].inner.n_rows == 1024
