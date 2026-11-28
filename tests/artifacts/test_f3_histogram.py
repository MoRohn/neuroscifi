"""Fix lane F3, item 6: one bin-edge convention for the histogram (found by fix lane F2's fuzz): a value on an inner
edge belongs to the upper bin, and the last bin is closed on the right; stated in the description, made so by the
generator (fix lane F1) and, independently, demanded by the product's check (fix lane F3)."""
from __future__ import annotations

import math
import re

from neuroscifi.artifacts import check, make
from neuroscifi.artifacts.make import charts


def spec(values, **options):
    return {"spec": "chart.histogram", "version": 1, "title": "Values", "description": "",
            "data": {"x": {"label": "value", "unit": None}, "values": list(values)}, "options": options,
            "sources": ["request"], "made_by": {"generator": "test", "version": 1}}


def bars(svg: str) -> list[tuple[float, float, float]]:
    """(x, width, height) of each drawn bin, left to right."""
    return sorted((float(x), float(w), float(h)) for x, w, h in re.findall(
        r'<rect class="mark" x="([\d.]+)" y="[\d.]+" width="([\d.]+)" height="([\d.]+)"', svg))


def test_the_largest_value_on_an_inner_edge_is_in_the_upper_bin():
    values = [11.0, 13.2, 15.1, 16.0, 16.4, 17.5]                       # 17.5 is the largest and lies on an edge
    assert charts.histogram_edges(values, {"bin_width": 2.5}) == [10.0, 12.5, 15.0, 17.5, 20.0]
    made = make.make(spec(values, bin_width=2.5))
    svg = made.bytes.decode()
    heights = [h for _x, _w, h in bars(svg)]
    unit = min(heights)
    assert [round(h / unit) for h in heights] == [1, 1, 3, 1]          # 10-12.5, 12.5-15, 15-17.5, 17.5-20
    assert "up to (not including) its right edge, the last bin also its right edge" in svg     # F1's words
    assert check.check(made.bytes, made.media_type, made.spec).ok


def test_edges_are_found_in_decimal_not_in_binary():
    # 0.3 / 0.1 is 2.9999999999999996 in binary: the value 0.3 still opens the bin 0.3-0.4
    assert charts.histogram_edges([0.1, 0.3], {"bin_width": 0.1}) == [0.1, 0.2, 0.3, 0.4]


def test_the_check_refuses_a_picture_that_counts_an_edge_value_in_the_bin_below(monkeypatch):
    def old_edges(values, opts):                                          # the rule before this fix
        w = float(opts["bin_width"])
        k0, k1 = math.floor(min(values) / w), math.ceil(max(values) / w)
        return [k * w for k in range(k0, max(k1, k0 + 1) + 1)]
    values = [11.0, 13.2, 15.1, 16.0, 16.4, 17.5]
    monkeypatch.setattr(charts, "histogram_edges", old_edges)
    made = make.make(spec(values, bin_width=2.5))
    assert len(bars(made.bytes.decode())) == 3                             # 17.5 drawn inside 15-17.5
    assert not check.check(made.bytes, made.media_type, made.spec).ok


def test_without_a_width_the_last_bin_is_closed_on_the_right():
    values = [0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0]
    made = make.make(spec(values))
    edges = charts.histogram_edges(values, {})
    assert edges[-1] == 10.0                                             # the largest value is the last edge
    assert check.check(made.bytes, made.media_type, made.spec).ok
