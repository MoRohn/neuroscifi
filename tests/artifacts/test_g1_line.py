"""Lane G1, first form: the line chart, its tick algorithm, its independent checker, and wrong artifacts caught."""
import ast
import copy
import math
from pathlib import Path

import pytest

import g1_support as S
from neuroscifi.artifacts import make, spec as spec_mod
from neuroscifi.artifacts.make import scales

HEART = {"x": {"label": "Week", "unit": None}, "y": {"label": "Heart rate", "unit": "beats per minute"},
         "series": [{"name": "Runner A", "points": [[w, 62 - 0.6 * w + (w % 3)] for w in range(1, 13)]},
                    {"name": "Runner B", "points": [[w, 58 - 0.3 * w + (w % 2)] for w in range(1, 13)]},
                    {"name": "Runner C", "points": [[w, 55 + 0.4 * w] for w in range(1, 13)], "flag": "warning"}]}


def line(**kw):
    return S.spec("chart_line", kw.pop("title", "Resting heart rate"), copy.deepcopy(kw.pop("data", HEART)),
                  kw.pop("options", {}))


# --- ticks ------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("lo,hi,target,labels", [
    (0, 1, 5, ("0.0", "0.2", "0.4", "0.6", "0.8", "1.0")),
    (3, 97, 5, ("0", "20", "40", "60", "80", "100")),
    (-12.3, 48, 5, ("-20", "0", "20", "40", "60")),
    (0.1, 0.3, 5, ("0.10", "0.15", "0.20", "0.25", "0.30")),
    (0, 7.3, 5, ("0", "2", "4", "6", "8")),
    (0, 1e6, 5, ("0", "200000", "400000", "600000", "800000", "1000000")),
])
def test_linear_ticks_are_nice_and_cover_the_data(lo, hi, target, labels):
    t = scales.linear_ticks(lo, hi, target)
    assert t.labels == labels
    assert t.lo <= lo and t.hi >= hi
    steps = {round(b - a, 12) for a, b in zip(t.values, t.values[1:])}
    assert len(steps) == 1                        # evenly spaced
    assert len(t.values) <= target + 2


def test_linear_ticks_never_print_float_noise_or_minus_zero():
    t = scales.linear_ticks(-0.3, 0.3, 6)
    assert all("e" not in s and s != "-0" and len(s) <= 5 for s in t.labels)
    assert scales.linear_ticks(5, 5, 5).lo < 5 < scales.linear_ticks(5, 5, 5).hi   # a constant gets a range


def test_log_ticks_are_powers_of_ten_in_plain_decimals():
    assert scales.log_ticks(3, 40).labels == ("1", "10", "100")
    assert scales.log_ticks(0.03, 4000).labels == ("0.01", "1", "100", "10000")
    t = scales.log_ticks(2, 9)
    assert t.labels == ("2", "5", "10")          # under two decades: 1, 2, 5 per decade; domain = enclosing ticks
    with pytest.raises(ValueError):
        scales.log_ticks(0, 10)


def test_time_ticks_are_calendar_aligned_iso_dates():
    day = 86400
    t = scales.time_ticks(1767225600, 1767225600 + 90 * day, 6)          # 2026-01-01 .. 2026-04-01
    assert t.labels == ("2026-01-01", "2026-02-01", "2026-03-01", "2026-04-01")
    t = scales.time_ticks(0, 5 * 3600, 6)
    assert t.labels[:2] == ("1970-01-01T00:00Z", "1970-01-01T01:00Z")
    t = scales.time_ticks(0, 30 * 365 * day, 6)
    assert all(s.endswith("-01-01") for s in t.labels)
    t = scales.time_ticks(4 * day + 1, 60 * day, 9)                        # weeks start on Monday
    assert scales.to_datetime(t.values[0]).weekday() == 0 or t.labels[0].endswith(("-01", "-15"))
    with pytest.raises(ValueError):
        scales.time_ticks(-1e13, 0, 5)


# --- making -----------------------------------------------------------------------------------------------------

def test_line_chart_follows_the_reading_contract():
    m = S.made(line())
    assert m.media_type == "image/svg+xml" and m.spec.made_by == {"generator": "g1.chart_line", "version": 1}
    root = S.tree(m.bytes)
    assert len(S.groups(root, "plot")) == 1
    assert len(S.groups(root, "axis", "x")) == 1 and len(S.groups(root, "axis", "y")) == 1
    names = [g.get("data-name") for g in S.groups(root, "series") if g.get("data-name")]
    assert names == ["Runner A", "Runner B", "Runner C"]
    for g in S.groups(root, "axis", "y"):
        for tick in [t for t in g.iter(S.q("g")) if "tick" in S.classes(t)]:
            assert tick.get("transform").startswith("translate(")
            assert float(next(tick.iter(S.q("text"))).text) == float(next(tick.iter(S.q("text"))).text)
    polylines = [e for e in root.iter(S.q("polyline")) if "series" in S.classes(e)]
    assert len(polylines) == 3
    assert {p.get("stroke-dasharray") for p in polylines} == {None, "9 4", "0.5 4.5"}   # told apart by dash
    # The source data is not echoed onto marks: no mark carries a data value attribute.
    for e in root.iter():
        assert not any(k.startswith("data-") and k != "data-name" and k != "data-form" for k in e.attrib)
    assert root.find(S.q("metadata")) is not None and root.find(S.q("title")).text == "Resting heart rate"
    assert "Line chart of Heart rate (beats per minute) against Week" in root.find(S.q("desc")).text


def test_line_chart_is_deterministic_and_checks_clean():
    a, b = S.made(line()), S.made(line())
    assert a.bytes == b.bytes
    v = S.verdict(a.bytes, line())
    assert v.ok, S.failed(v)
    rec = S.finding(v, "values_recovered")
    assert rec.passed and rec.measure == 1.0
    assert S.finding(v, "contrast_text").measure >= 4.5


def test_hue_only_on_the_flagged_series():
    root = S.tree(S.made(line()).bytes)
    for e in root.iter():
        for attr in ("fill", "stroke"):
            value = e.get(attr, "")
            if "warn" in value or "danger" in value:
                assert "warning" in S.classes(e) or any(
                    "warning" in S.classes(a) for a in root.iter() if e in list(a.iter()))


def test_time_axis_line_chart_recovers_every_point():
    data = {"x": {"label": "Time", "unit": None, "scale": "time"}, "y": {"label": "Charge", "unit": "%"},
            "series": [{"name": "Phone", "points": [[1767225600 + h * 3600, 100 - h * 3.2] for h in range(25)]}]}
    s = line(data=data, title="Battery charge")
    v = S.verdict(S.made(s).bytes, s)
    assert v.ok, S.failed(v)
    assert S.finding(v, "values_recovered").measure == 1.0


def test_log_axis_and_gaps():
    pts = [[x, 10 ** (x / 3)] for x in range(0, 13)]
    pts[5][1] = None                                          # a gap: two polylines
    data = {"x": {"label": "Step", "unit": None}, "y": {"label": "Size", "unit": "bytes", "scale": "log"},
            "series": [{"name": "growth", "points": pts}]}
    s = line(data=data)
    m = S.made(s)
    assert len([e for e in S.tree(m.bytes).iter(S.q("polyline"))]) == 2
    assert any("missing" in n for n in m.notes)
    v = S.verdict(m.bytes, s)
    assert v.ok, S.failed(v)
    assert S.finding(v, "values_recovered").measure == 1.0


def test_aggregation_is_stated_and_checked_against_the_stated_rule():
    pts = [[i, math.sin(i / 50) * 10 + (i % 7)] for i in range(5000)]
    data = {"x": {"label": "Sample", "unit": None}, "y": {"label": "Signal", "unit": "mV"},
            "series": [{"name": "trace", "points": pts}]}
    raw = line(data=data)
    v = S.verdict(S.made(raw).bytes, raw)
    assert S.finding(v, "points_exceed_pixels") is not None          # a style finding: more points than pixels
    agg = line(data=data, options={"aggregate": {"rule": "min_max", "bins": 200}})
    m = S.made(agg)
    assert "min_max" not in m.bytes.decode() and "smallest and its largest" in m.bytes.decode()
    v = S.verdict(m.bytes, agg)
    assert v.ok, S.failed(v)
    assert S.finding(v, "values_recovered").measure == 1.0 and S.finding(v, "points_exceed_pixels") is None


def test_invalid_specs_are_refused_before_making():
    with pytest.raises(ValueError):
        make.make(line(data={"x": {"label": "x"}, "y": {"label": "y"}, "series": []}))
    with pytest.raises(ValueError):
        make.make(line(options={"width": 10}))
    bad = line()
    bad["data"]["series"][0]["points"][0] = [1, "high"]
    assert spec_mod.validate(bad)


@pytest.mark.xfail(reason="waits for lane K's relaxed content rule", strict=True)
def test_markup_in_a_label_is_escaped_text():
    data = {**HEART, "series": [{"name": "<script>alert(1)</script> & <b>", "points": [[1, 2], [2, 3]]},
                                {"name": "https://example.invalid/x", "points": [[1, 1], [2, 1]]}]}
    s = line(data=data, title="p < 0.05 & <i>bold</i> C:\\temp")
    m = S.made(s)
    text = m.bytes.decode()
    assert "<script" not in text and "&lt;script&gt;" in text and "<i>" not in text
    v = S.verdict(m.bytes, s)
    assert v.ok, S.failed(v)


def test_comparison_operators_in_titles_work_today():
    s = line(title="Rates where p < 0.05 and q > 2")
    m = S.made(s)
    assert "p &lt; 0.05 and q &gt; 2" in m.bytes.decode()
    assert S.verdict(m.bytes, s).ok


# --- wrong artifacts --------------------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def good():
    s = line()
    return s, S.made(s).bytes


def test_the_right_one_passes(good):
    s, data = good
    assert S.verdict(data, s).ok


@pytest.mark.parametrize("mutate,rule", [
    (lambda d: S.scale_ticks(d, "y", 1.1), "values_recovered"),          # a scale off by 10 %
    (S.swap_series, "values_recovered"),                                 # two series swapped
    (S.clip_title, "text_inside_canvas"),                                # a clipped label
    (S.add_script, "svg_allowed_elements"),                              # a script element
    (S.green_fill, "no_green"),                                          # a green fill
    (S.drop_title, "title_and_description"),                             # a missing title
    (S.empty_plot, "has_marks"),                                         # an empty chart
    (lambda d: S.shift_marks(d, 400), "marks_inside_plot"),              # marks outside the plot
    (S.faint_text, "contrast_text"),                                     # text too faint
    (S.same_dash, "series_distinguishable"),                             # series look alike
    (S.add_event, "svg_no_event_attributes"),                            # an event attribute
    (S.add_external_image, "svg_no_href"),                               # an external reference
])
def test_wrong_line_charts_are_caught(good, mutate, rule):
    s, data = good
    v = S.verdict(mutate(data), s)
    assert not v.ok and rule in S.failed(v), (rule, S.failed(v))


def test_one_tick_label_off_by_ten_percent_is_an_inconsistent_axis(good):
    s, data = good
    root = S.tree(data)
    tick = [t for g in S.groups(root, "axis", "y") for t in g.iter(S.q("text"))][2]
    tick.text = repr(round(float(tick.text) * 1.1, 4))
    v = S.verdict(S.to_bytes(root), s)
    assert "axis_ticks_consistent" in S.failed(v)


def test_doctype_and_entities_are_refused(good):
    s, data = good
    hostile = b'<?xml version="1.0"?><!DOCTYPE svg [<!ENTITY x "boom">]>' + data
    v = S.verdict(hostile, s)
    assert not v.ok and "svg_parse" in S.failed(v)
    pi = b'<?xml-stylesheet href="https://example.invalid/x.css"?>' + data
    assert "svg_parse" in S.failed(S.verdict(pi, s))


def test_unchecked_is_never_reported_as_checked():
    assert not S.verdict(b"not svg at all", None).ok


# --- independence of make and check ------------------------------------------------------------------------------

def test_check_modules_never_import_make():
    root = Path(__file__).resolve().parents[2] / "src" / "neuroscifi" / "artifacts" / "check"
    for path in root.glob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                names = [a.name for a in node.names]
                assert "make" not in mod.split("."), (path.name, mod)
                assert not (node.level >= 2 and mod == "" and "make" in names), path.name
                assert not (node.level == 2 and mod.startswith("make")), path.name
            elif isinstance(node, ast.Import):
                assert not any("artifacts.make" in a.name for a in node.names), path.name
