"""Lane G1: bar (grouped, stacked), scatter, histogram, heatmap and small multiples; carried information and wrong
artifacts for each."""
import copy
import random

import pytest

import g1_support as S
from neuroscifi.artifacts import make

RAIN = {"categories": ["Jan", "Feb", "Mar", "Apr", "May", "Jun"], "x": {"label": "Month"},
        "y": {"label": "Rainfall", "unit": "mm"},
        "series": [{"name": "Lisbon", "values": [99, 88, 61, 64, 47, 14]},
                   {"name": "Dublin", "values": [76, 56, 55, 59, 58, 66], "danger": [5]},
                   {"name": "Oslo", "values": [49, 36, None, 41, 53, 65]}]}


def _rng():
    return random.Random(11)


def scatter_data():
    rng = _rng()
    return {"x": {"label": "Engine size", "unit": "litres"}, "y": {"label": "Fuel use", "unit": "L per 100 km"},
            "series": [{"name": n, "points": [[round(1 + rng.random() * 3, 3), round(4 + k + rng.random() * 4, 3)]
                                              for _ in range(30)]}
                       for k, n in enumerate(["Petrol", "Diesel", "Hybrid", "Gas", "Other", "Electric range"])]}


def hist_data():
    rng = _rng()
    return {"x": {"label": "Reaction time", "unit": "ms"}, "values": [round(rng.gauss(250, 40), 1) for _ in range(600)]
            + [200.0, 200.0, 250.0]}            # values exactly on bin edges


def heat_data():
    return {"x": {"label": "Hour", "categories": [f"{h:02d}" for h in range(8, 20)]},
            "y": {"label": "Day", "categories": ["Mon", "Tue", "Wed", "Thu", "Fri"]},
            "z": {"label": "Occupancy", "unit": "%"},
            "values": [[None if (d, h) == (2, 9) else round(30 + 5 * d + 4 * abs(h - 13), 1) for h in range(8, 20)]
                       for d in range(5)]}


def multiples_data(n=8):
    return {"x": {"label": "Day", "unit": None}, "y": {"label": "Temperature", "unit": "C"},
            "series": [{"name": f"City {k}", "points": [[d, round(10 + 0.3 * d * (k % 3) - k, 2)] for d in range(1, 31)]}
                       for k in range(n)]}


CASES = {
    "bar": ("chart.bar", RAIN, {}),
    "stacked": ("chart.bar", {**RAIN, "series": [{"name": "Grid", "values": [12, 14, 9, 11, 15, 10]},
                                                 {"name": "Solar", "values": [6, 4, 8, 7, 3, 0]},
                                                 {"name": "Battery", "values": [2, 1, 3, 2, 1, 4]}]},
                {"mode": "stacked"}),
    "scatter": ("chart.scatter", None, {}),
    "histogram": ("chart.histogram", None, {}),
    "heatmap": ("chart.heatmap", None, {}),
    "multiples": ("chart.small_multiples", None, {}),
}
DATA = {"scatter": scatter_data, "histogram": hist_data, "heatmap": heat_data, "multiples": multiples_data}


def case(name, **options):
    form, data, opts = CASES[name]
    data = copy.deepcopy(data) if data is not None else DATA[name]()
    return S.spec(form, f"A {name} chart", data, {**opts, **options})


@pytest.mark.parametrize("name", list(CASES))
def test_each_chart_is_deterministic_and_carries_all_its_values(name):
    s = case(name)
    a, b = S.made(s), S.made(s)
    assert a.bytes == b.bytes
    v = S.verdict(a.bytes, s)
    assert v.ok, [(f.rule, f.detail) for f in v.technical if not f.passed]
    rec = S.finding(v, "values_recovered")
    assert rec.passed and rec.measure == 1.0, rec.detail
    assert S.finding(v, "marks_match_source").passed


def test_bars_start_at_zero_and_missing_values_draw_nothing():
    s = case("bar")
    root = S.tree(S.made(s).bytes)
    oslo = next(g for g in S.groups(root, "series") if g.get("data-name") == "Oslo")
    assert len([r for r in oslo.iter(S.q("rect"))]) == 5               # one value is missing
    ticks = [t.text for g in S.groups(root, "axis", "y") for t in g.iter(S.q("text")) if "label" not in S.classes(t)]
    assert ticks[0] == "0"
    flagged = [r for r in root.iter(S.q("rect")) if "danger" in S.classes(r)]
    assert len(flagged) == 1 and flagged[0].get("stroke") == "var(--nsf-danger)"
    fills = {r.get("fill") for g in S.groups(root, "series") for r in g.iter(S.q("rect"))}
    assert any(f.startswith("url(#") for f in fills)                   # a hatch pattern tells a series apart


def test_a_truncated_value_axis_is_a_finding():
    s = case("bar", y_min=40)
    m = S.made(s)                            # y_min above some values is not honoured, and the note says so
    assert any("y_min" in n for n in m.notes) and S.verdict(m.bytes, s).ok
    high = {"categories": ["a", "b", "c", "d"], "x": {"label": "Item"}, "y": {"label": "Score"},
            "series": [{"name": "s", "values": [80, 85, 90, 96]}]}
    assert S.verdict(S.made(S.spec("chart.bar", "Scores", high)).bytes, S.spec("chart.bar", "Scores", high)).ok
    s2 = S.spec("chart.bar", "Scores", high, {"y_from_zero": False})
    m = S.made(s2)
    assert any("not start at zero" in n for n in m.notes)
    assert "not from zero" in S.tree(m.bytes).find(S.q("desc")).text
    v2 = S.verdict(m.bytes, s2)
    assert S.finding(v2, "axis_truncated") is not None and v2.ok     # asked for: a style finding
    v3 = S.verdict(m.bytes, S.spec("chart.bar", "Scores", high))      # not asked for: technical
    assert not v3.ok and "bars_from_zero" in S.failed(v3)


def test_stacked_bars_refuse_negative_values():
    bad = case("stacked")
    bad["data"]["series"][0]["values"][0] = -3
    with pytest.raises(ValueError, match="zero or more"):
        S.made(bad)


def test_a_category_count_mismatch_is_refused():
    bad = case("bar")
    bad["data"]["series"][0]["values"].append(1)
    with pytest.raises(ValueError):
        S.made(bad)


def test_too_many_categories_for_the_width_are_refused_with_the_way_out():
    data = {"categories": [f"Category number {i}" for i in range(80)], "x": {"label": "C"}, "y": {"label": "V"},
            "series": [{"name": "s", "values": list(range(80))}]}
    with pytest.raises(ValueError, match="wider"):
        S.made(S.spec("chart.bar", "Many", data))
    assert S.verdict(S.made(S.spec("chart.bar", "Many", data, {"width": 2000})).bytes,
                     S.spec("chart.bar", "Many", data, {"width": 2000})).ok


def test_long_categories_are_rotated_and_keep_their_full_text():
    data = {"categories": ["Greater Tokyo Area", "Delhi National Capital Region", "Shanghai", "Greater Sao Paulo",
                           "Mexico City Metropolitan Area", "Greater Cairo", "Mumbai Metropolitan Region"],
            "x": {"label": "Area"}, "y": {"label": "Population", "unit": "millions"},
            "series": [{"name": "2025", "values": [37.0, 33.8, 29.9, 22.8, 22.5, 22.6, 21.7]}]}
    s = S.spec("chart.bar", "Largest areas", data)
    m = S.made(s)
    root = S.tree(m.bytes)
    labels = [t for g in S.groups(root, "axis", "x") for t in g.iter(S.q("text")) if "rotate(" in t.get("transform", "")]
    assert sorted(t.text for t in labels) == sorted(data["categories"])     # read as their text: never cut
    assert all(t.find(S.q("title")) is None for t in labels)
    v = S.verdict(m.bytes, s)
    assert v.ok and S.finding(v, "categories_labelled").passed


def test_scatter_series_differ_by_marker_shape():
    root = S.tree(S.made(case("scatter")).bytes)
    sigs = set()
    for g in S.groups(root, "series"):
        first = list(g)[:2]
        sigs.add(tuple((e.tag, e.get("r"), e.get("fill", "")[:10], e.get("d", "").count("L")) for e in first))
    assert len(sigs) == 6


def test_histogram_bins_are_stated_and_edge_values_counted_once():
    s = case("histogram")
    m = S.made(s)
    desc = S.tree(m.bytes).find(S.q("desc")).text
    assert "a value on an inner edge belongs to the upper bin, and the last bin is closed on the right" in desc
    v = S.verdict(m.bytes, s)
    assert S.finding(v, "all_values_binned").passed
    s2 = case("histogram", bin_width=25)
    v2 = S.verdict(S.made(s2).bytes, s2)
    assert v2.ok, S.failed(v2)


def test_heatmap_prints_values_when_cells_are_few_and_marks_missing_cells():
    s = case("heatmap")
    m = S.made(s)
    root = S.tree(m.bytes)
    assert any("missing" in S.classes(r) for r in root.iter(S.q("rect")))
    printed = [t for t in root.iter(S.q("text")) if "value" in S.classes(t)]
    assert len(printed) == 59
    fills = {r.get("fill") for r in root.iter(S.q("rect")) if "mark" in S.classes(r)}
    assert all(f[1:3] == f[3:5] == f[5:7] for f in fills)              # gray ramp only
    v = S.verdict(m.bytes, s)
    assert S.finding(v, "printed_values_match").passed
    s_off = case("heatmap", print_values="off")
    assert not [t for t in S.tree(S.made(s_off).bytes).iter(S.q("text")) if "value" in S.classes(t)]


def test_small_multiples_share_one_scale():
    s = case("multiples")
    root = S.tree(S.made(s).bytes)
    panels = S.groups(root, "panel")
    assert len(panels) == 8
    first_ticks = None
    for p in panels:
        ticks = [t.text for g in p.iter(S.q("g")) if {"axis", "y"} <= S.classes(g) for t in g.iter(S.q("text"))]
        first_ticks = first_ticks or ticks
        assert ticks == first_ticks


def test_more_series_than_styles_is_a_finding_on_a_line_chart():
    s = S.spec("chart.line", "Eight cities", multiples_data(8))
    m = S.made(s)
    assert any("share" in n for n in m.notes)
    v = S.verdict(m.bytes, s)
    assert not v.ok and "series_distinguishable" in S.failed(v)


# --- wrong artifacts --------------------------------------------------------------------------------------------

def _swap_bar_values(data):
    root = S.tree(data)
    rects = [r for g in S.groups(root, "series")[:1] for r in g.iter(S.q("rect"))]
    a, b = rects[0], rects[1]
    for k in ("y", "height"):
        va, vb = a.get(k), b.get(k)
        a.set(k, vb)
        b.set(k, va)
    return S.to_bytes(root)


def _recolor_cell(data):
    root = S.tree(data)
    cell = next(r for r in root.iter(S.q("rect")) if "mark" in S.classes(r))
    cell.set("fill", "#101010" if cell.get("fill") > "#80" else "#f0f0f0")
    return S.to_bytes(root)


def _wrong_printed(data):
    root = S.tree(data)
    t = next(t for t in root.iter(S.q("text")) if "value" in S.classes(t))
    t.text = str(float(t.text) + 7)
    return S.to_bytes(root)


def _drop_bin(data):
    root = S.tree(data)
    g = S.groups(root, "series")[0]
    g.remove(list(g)[3])
    return S.to_bytes(root)


@pytest.mark.parametrize("name,mutate,rule", [
    ("bar", lambda d: S.scale_ticks(d, "y", 1.1), "values_recovered"),
    ("bar", S.swap_series, "values_recovered"),
    ("bar", _swap_bar_values, "values_recovered"),
    ("bar", S.green_fill, "no_green"),
    ("bar", S.add_script, "svg_allowed_elements"),
    ("bar", S.external_url_fill, "svg_no_external_reference"),
    ("stacked", lambda d: S.scale_ticks(d, "y", 1.1), "values_recovered"),
    ("scatter", lambda d: S.scale_ticks(d, "x", 1.1), "values_recovered"),
    ("scatter", S.swap_series, "values_recovered"),
    ("scatter", S.drop_title, "title_and_description"),
    ("histogram", lambda d: S.scale_ticks(d, "y", 1.1), "values_recovered"),
    ("histogram", _drop_bin, "all_values_binned"),
    ("heatmap", _recolor_cell, "values_recovered"),
    ("heatmap", _wrong_printed, "printed_values_match"),
    ("heatmap", S.clip_title, "text_inside_canvas"),
    ("multiples", lambda d: S.shift_marks(d, 9), "values_recovered"),
])
def test_wrong_charts_are_caught(name, mutate, rule):
    s = case(name)
    good = S.made(s).bytes
    assert S.verdict(good, s).ok
    v = S.verdict(mutate(good), s)
    assert not v.ok and rule in S.failed(v), (rule, S.failed(v))


def test_catalog_lists_every_g1_chart():
    rows = {r["form"]: r for r in make.catalog()}
    for form in ("chart.line", "chart.bar", "chart.scatter", "chart.histogram", "chart.heatmap",
                 "chart.small_multiples"):
        assert rows[form]["available"] and rows[form]["generator"] == "g1." + form


def test_a_value_on_a_bin_edge_belongs_to_the_bin_it_starts_even_the_largest():
    """Fix lane F1 (lane F2's fuzz: 17.5 drawn in the lower bin against the request's half-open bins): bins are
    [k*w, (k+1)*w), and the last edge lies above the largest value, so a largest value on k*w opens bin k."""
    from neuroscifi.artifacts.make.charts import histogram_edges
    values = [1.0, 2.5, 5.0, 12.4, 17.5]
    edges = histogram_edges(values, {"bin_width": 2.5})
    assert edges[0] == 0.0 and edges[-1] == 20.0 and edges[-2] == 17.5
    assert histogram_edges([0.3, 0.7], {"bin_width": 0.1}) == [0.3, 0.4, 0.5, 0.6, 0.7, 0.8]
    s = case("histogram", bin_width=2.5)
    s["data"]["values"] = values
    v = S.verdict(S.made(s).bytes, s)
    assert v.ok, S.failed(v)
