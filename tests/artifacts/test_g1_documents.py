"""Lane G1: the HTML table, the data files (CSV, JSON) and the report documents (HTML, Markdown): exact round trips,
document structure, and deliberately wrong ones caught."""
import copy
import json

import numpy as np
import pytest

import g1_support as S
from neuroscifi.artifacts import types as T

T0 = 1767225600
TABLE = {"columns": [
    {"name": "City", "kind": "text", "values": ["Lisbon", "Dublin", "Oslo", "=SUM(A1)", "'quoted", "", "a, \"b\""]},
    {"name": "Rainfall", "kind": "number", "unit": "mm", "values": [99.5, 76.25, None, 0.1 + 0.2, 1e-05, -0.0, 1e300]},
    {"name": "Rainy days", "kind": "integer", "values": [14, 13, 8, 0, -2, 2 ** 60, None]},
    {"name": "Coastal", "kind": "boolean", "values": [True, True, False, None, False, True, False]},
    {"name": "Measured", "kind": "time", "values": [T0, T0 + 86400, T0 + 0.25, None, T0 + 3600, 0, -86400.5]}]}


def tspec(form, data=None, options=None, title="Rainfall in three cities"):
    return S.spec(form, title, copy.deepcopy(data or TABLE), options or {}, description="January averages.")


# --- the HTML table ---------------------------------------------------------------------------------------------

def test_html_table_is_real_and_every_cell_matches():
    s = tspec("table.html")
    m = S.made(s)
    text = m.bytes.decode()
    assert m.media_type == "text/html" and "<script" not in text and "style=" not in text and "href" not in text
    assert '<th scope="col">Rainfall <span class="unit">(mm)</span></th>' in text
    assert '<th scope="row">Lisbon</th>' in text and "<caption>" in text
    assert ">0.30000000000000004<" in text and ">0.00001<" in text and ">1152921504606846976<" in text
    assert "1969-12-30T23:59:59.5Z" in text and "&quot;b&quot;" in text
    v = S.verdict(m.bytes, s, "text/html")
    assert v.ok, S.failed(v)
    assert S.finding(v, "cells_match").measure == 1.0
    assert S.made(s).bytes == m.bytes


def test_decimals_are_stated_and_checked_to_their_precision():
    s = tspec("table.html", options={"decimals": 1})
    m = S.made(s)
    assert "rounded to 1 decimal places" in m.bytes.decode() and ">0.3<" in m.bytes.decode()
    assert S.verdict(m.bytes, s, "text/html").ok


def _edit(data, old, new):
    text = data.decode()
    assert old in text
    return text.replace(old, new, 1).encode()


@pytest.mark.parametrize("mutate,rule", [
    (lambda d: _edit(d, ">99.5<", ">99.6<"), "cells_match"),
    (lambda d: _edit(d, ">Dublin<", ">Cork<"), "cells_match"),
    (lambda d: _edit(d, "<caption>", "<caption hidden>").replace(b"<caption hidden>", b"<span>").replace(
        b"</caption>", b"</span>"), "table_structure"),
    (lambda d: _edit(d, "</tbody>", "<tr><td><script>alert(1)</script></td></tr></tbody>"), "html_no_script"),
    (lambda d: _edit(d, ">Oslo<", '><a href="https://example.invalid">Oslo</a><'), "links_text_only"),
    (lambda d: _edit(d, "<td", '<td onclick="x()"'), "html_allowed_attributes"),
    (lambda d: _edit(d, 'scope="col">Rainfall', 'scope="col">Rain'), "headers_match"),
])
def test_wrong_tables_are_caught(mutate, rule):
    s = tspec("table.html")
    good = S.made(s).bytes
    v = S.verdict(mutate(good), s, "text/html")
    assert not v.ok and rule in S.failed(v), (rule, S.failed(v))


# --- data files -------------------------------------------------------------------------------------------------

def test_csv_round_trips_exactly_under_its_dialect():
    s = tspec("data.csv")
    m = S.made(s)
    raw = m.bytes
    assert m.media_type == "text/csv" and not raw.startswith(b"\xef\xbb\xbf") and raw.endswith(b"\r\n")
    assert b"\n" not in raw.replace(b"\r\n", b"")
    lines = raw.decode().split("\r\n")
    assert lines[0] == "City,Rainfall [mm],Rainy days,Coastal,Measured"
    assert lines[4].startswith("'=SUM(A1),0.30000000000000004,")          # the formula guard
    assert lines[5].startswith("''quoted,1e-05,")
    assert lines[3].startswith("Oslo,,8,")                                  # missing: empty, unquoted
    assert lines[6].startswith('"",-0.0,')                                  # empty text: quoted
    v = S.verdict(raw, s, "text/csv")
    assert v.ok, S.failed(v)
    assert S.finding(v, "values_exact").measure == 1.0


def test_json_round_trips_exactly():
    s = tspec("data.json")
    m = S.made(s)
    doc = json.loads(m.bytes)
    assert doc["dialect"] == "neuroscifi.table/1" and "not evidence" in doc["made_by"]
    assert doc["columns"][1]["values"][3] == 0.1 + 0.2
    v = S.verdict(m.bytes, s, "application/json")
    assert v.ok, S.failed(v)


def test_data_files_compare_with_a_decoded_table():
    table = T.Table((T.Column("height", "number", "m", np.array([1.5, np.nan, 2.25]), 1),
                     T.Column("name", "text", None, ("a", "b", "c"), 0)), 3)
    data = {"columns": [{"name": "height", "kind": "number", "unit": "m", "values": [1.5, None, 2.25]},
                        {"name": "name", "kind": "text", "values": ["a", "b", "c"]}]}
    for form, media in (("data.csv", "text/csv"), ("data.json", "application/json")):
        m = S.made(S.spec(form, "Heights", data))
        assert S.verdict(m.bytes, None, media, source=table).ok
        other = T.Table((T.Column("height", "number", "m", np.array([1.5, np.nan, 2.5]), 1),
                         T.Column("name", "text", None, ("a", "b", "c"), 0)), 3)
        assert "values_exact" in S.failed(S.verdict(m.bytes, None, media, source=other))


def test_cells_of_the_wrong_kind_are_refused():
    bad = copy.deepcopy(TABLE)
    bad["columns"][2]["values"][0] = 1.5
    with pytest.raises(ValueError, match="integer"):
        S.made(S.spec("data.csv", "Bad", bad))
    bad = copy.deepcopy(TABLE)
    bad["columns"][0]["values"].append("extra")
    with pytest.raises(ValueError, match="same number"):
        S.made(S.spec("data.json", "Bad", bad))


@pytest.mark.parametrize("form,media,mutate,rule", [
    ("data.csv", "text/csv", lambda d: d.replace(b"99.5", b"99.50001", 1), "values_exact"),
    ("data.csv", "text/csv", lambda d: d.replace(b"\r\n", b"\n"), "csv_dialect"),
    ("data.csv", "text/csv", lambda d: b"\xef\xbb\xbf" + d, "csv_dialect"),
    ("data.csv", "text/csv", lambda d: d.replace(b"'=SUM", b"=SUM", 1), "formula_guard"),
    ("data.csv", "text/csv", lambda d: d.replace(b" [mm]", b"", 1), "header_matches"),
    ("data.csv", "text/csv", lambda d: d.replace(b"Oslo,,", b'Oslo,"",', 1), "values_exact"),
    ("data.json", "application/json", lambda d: d.replace(b"99.5", b"NaN", 1), "json_parses"),
    ("data.json", "application/json", lambda d: d.replace(b'"title"', b'"dialect":"x","title"', 1), "json_parses"),
    ("data.json", "application/json", lambda d: d.replace(b"76.25", b"76.26", 1), "values_exact"),
    ("data.json", "application/json", lambda d: d.replace(b"not evidence", b"evidence", 1), "made_by_label"),
])
def test_wrong_data_files_are_caught(form, media, mutate, rule):
    s = tspec(form)
    good = S.made(s).bytes
    assert S.verdict(good, s, media).ok
    v = S.verdict(mutate(good), s, media)
    assert not v.ok and rule in S.failed(v), (rule, S.failed(v))


# --- documents --------------------------------------------------------------------------------------------------

BAR = S.spec("chart.bar", "Rainfall by month and city",
             {"categories": ["Jan", "Feb", "Mar"], "x": {"label": "Month"}, "y": {"label": "Rainfall", "unit": "mm"},
              "series": [{"name": "Lisbon", "values": [99, 88, 61]}, {"name": "Oslo", "values": [49, 36, 47]}]})
DOC = {"blocks": [
    {"kind": "heading", "level": 1, "text": "What was asked"},
    {"kind": "paragraph", "text": "How much does it rain in January?"},
    {"kind": "heading", "level": 1, "text": "What the data shows"},
    {"kind": "paragraph", "parts": [{"text": "Lisbon is wettest ("}, {"ref": "rain"}, {"text": "); see "},
                                    {"ref": "months"}, {"text": "."}]},
    {"kind": "figure", "id": "months", "caption": "Monthly rainfall", "figure": BAR},
    {"kind": "table", "id": "rain", "caption": "January rainfall", "table": {"columns": TABLE["columns"][:3]}},
    {"kind": "heading", "level": 2, "text": "Caveats"},
    {"kind": "list_item", "text": "Averages hide swings (p < 0.05 was not tested)."},
    {"kind": "list_item", "level": 2, "text": "Snow counts as rain."},
    {"kind": "list_item", "text": "# not a heading, *not emphasis*, [not a link](nowhere)"},
    {"kind": "note", "text": "Made from the numbers above."},
    {"kind": "heading", "level": 1, "text": "Method"},
    {"kind": "code", "text": "rain = mean(monthly)\n```\nstill code"},
    {"kind": "equation", "text": "mean = (x_1 + x_2) / 2"}],
    "sources": [{"id": "ipma", "title": "Climate normals, Lisbon", "detail": "1991 to 2020"}]}


def dspec(form, data=None, **kw):
    return S.spec(form, kw.pop("title", "January rainfall"), copy.deepcopy(data or DOC), kw.pop("options", {}),
                  description="A short report.")


def test_html_document_is_self_contained_labelled_and_checked():
    s = dspec("document.html")
    m = S.made(s)
    text = m.bytes.decode()
    assert text.startswith("<!DOCTYPE html>") and '<html lang="en">' in text
    assert "<script" not in text and "src=" not in text and "Content-Security-Policy" in text
    assert text.count('class="made-by"') == 2 and "What this is based on" in text
    assert '<a href="#ref-rain">Table 1</a>' in text and '<a href="#ref-months">Figure 1</a>' in text
    assert '<svg xmlns="http://www.w3.org/2000/svg"' in text and '<figure id="ref-months">' in text
    assert "[not a link](nowhere)" in text                        # text, not interpreted
    v = S.verdict(m.bytes, s, "text/html")
    assert v.ok, [(f.rule, f.detail) for f in v.technical if not f.passed]
    assert S.finding(v, "figures_safe").passed
    assert S.made(s).bytes == m.bytes


def test_markdown_document_is_text_safe_and_checked():
    s = dspec("document.md")
    m = S.made(s)
    text = m.bytes.decode()
    assert text.startswith("> Made by NeuroSciFi")
    assert "\\# not a heading, \\*not emphasis\\*, \\[not a link\\](nowhere)" in text
    assert "p \\< 0.05" in text and "````" in text                      # a fence longer than the code's own
    assert "Table 1" in text and "](#" not in text
    v = S.verdict(m.bytes, s, "text/markdown")
    assert v.ok, [(f.rule, f.detail) for f in v.technical if not f.passed]


def test_documents_refuse_bad_structure():
    skip = copy.deepcopy(DOC)
    skip["blocks"][6]["level"] = 3
    with pytest.raises(ValueError, match="skip"):
        S.made(dspec("document.html", skip))
    dangling = copy.deepcopy(DOC)
    dangling["blocks"][3]["parts"][1]["ref"] = "nothing"
    with pytest.raises(ValueError, match="no figure or table"):
        S.made(dspec("document.md", dangling))
    not_svg = copy.deepcopy(DOC)
    not_svg["blocks"][4]["figure"] = tspec("table.html")
    with pytest.raises(ValueError, match="SVG"):
        S.made(dspec("document.html", not_svg))
    bad_fig = copy.deepcopy(DOC)
    bad_fig["blocks"][4]["figure"]["data"]["series"] = []
    with pytest.raises(ValueError):
        S.made(dspec("document.html", bad_fig))


def test_addresses_and_markup_in_documents_stay_text():
    data = copy.deepcopy(DOC)
    data["sources"].append({"title": "Met Eireann <averages>", "address": "https://www.met.ie/climate"})
    data["blocks"][1]["text"] = "<script>alert(1)</script> and C:\\temp and /etc/passwd"
    for form, media in (("document.html", "text/html"), ("document.md", "text/markdown")):
        s = dspec(form, data, title="Rain <b>now</b>")
        m = S.made(s)
        text = m.bytes.decode()
        assert "<script>" not in text and "<b>" not in text
        assert "https://www.met.ie/climate" in text
        v = S.verdict(m.bytes, s, media)
        assert v.ok and S.finding(v, "links_text_only").passed, S.failed(v)


def _doc_edit(old, new):
    return lambda d: _edit(d, old, new)


@pytest.mark.parametrize("form,media,mutate,rule", [
    ("document.html", "text/html", _doc_edit("How much does it rain", "TODO: how much does it rain"), "no_placeholders"),
    ("document.html", "text/html", _doc_edit("<h3 id=", "<h5 id="), "heading_levels"),
    ("document.html", "text/html", _doc_edit("<p>How much does it rain in January?</p>", ""), "no_empty_sections"),
    ("document.html", "text/html", _doc_edit('href="#ref-rain"', 'href="https://example.invalid"'), "links_text_only"),
    ("document.html", "text/html", _doc_edit('href="#ref-rain"', 'href="#nowhere"'), "references_resolve"),
    ("document.html", "text/html", lambda d: d.replace(b'class="made-by"', b'class="byline"'), "made_by_label"),
    ("document.html", "text/html", _doc_edit("</main>", "<script>alert(1)</script></main>"), "html_no_script"),
    ("document.html", "text/html", _doc_edit("<rect class=\"paper\"", "<script>x</script><rect class=\"paper\""),
     "figures_safe"),
    ("document.html", "text/html", _doc_edit("<figcaption>Figure 1. Monthly rainfall</figcaption>", ""),
     "figures_captioned"),
    ("document.html", "text/html", _doc_edit("<style>", "<style>@import 'x.css';"), "css_safe"),
    ("document.html", "text/html", _doc_edit(">99.5<", ">99.6<"), "cells_match"),
    ("document.md", "text/markdown", _doc_edit("| Lisbon | 99.5 |", "| Lisbon | 99.6 |"), "cells_match"),
    ("document.md", "text/markdown", _doc_edit("| Dublin |", "| Cork |"), "cells_match"),
    ("document.md", "text/markdown", _doc_edit("How much does it rain", "Lorem ipsum, how much does it rain"),
     "no_placeholders"),
    ("document.md", "text/markdown", _doc_edit("## What was asked", "#### What was asked"), "heading_levels"),
    ("document.md", "text/markdown", _doc_edit("How much", "[See](https://example.invalid) how much"),
     "links_text_only"),
    ("document.md", "text/markdown", _doc_edit("How much", "<img src=x onerror=alert(1)> how much"), "no_raw_html"),
    ("document.md", "text/markdown", lambda d: d.replace(b"> Made by NeuroSciFi", b"> Written by someone"),
     "made_by_label"),
    ("document.md", "text/markdown", _doc_edit("<rect class=\"paper\"", "<foreignObject/><rect class=\"paper\""),
     "figures_safe"),
])
def test_wrong_documents_are_caught(form, media, mutate, rule):
    s = dspec(form)
    good = S.made(s).bytes
    v = S.verdict(mutate(good), s, media)
    assert not v.ok and rule in S.failed(v), (rule, S.failed(v))


def test_a_foreign_form_is_left_to_its_own_validator():
    v = S.verdict(b"<!DOCTYPE html><html><body><script>runtime()</script></body></html>",
                  S.spec("explorable_model", "x"), "text/html")
    assert v.technical == () and not v.ok          # never "checked"; G1 gives no verdict on another lane's form
