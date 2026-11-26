"""Lane G1: the animated SVG (declarative, reduced motion honoured by a still last frame) and bounded repair."""
import copy
import math

import pytest

import g1_support as S
from neuroscifi.artifacts import check, make, spec as spec_mod
from neuroscifi.artifacts.make.repair import MAX_ROUNDS, repair, repair_loop


def bar_frames(n=4):
    return [{"label": str(2020 + k),
             "data": {"categories": ["Jan", "Feb", "Mar", "Apr"], "x": {"label": "Month"},
                      "y": {"label": "Rainfall", "unit": "mm"},
                      "series": [{"name": "Lisbon", "values": [90 + 5 * k, 80 - 3 * k, 60 + k, 50]},
                                 {"name": "Oslo", "values": [40 + 2 * k, 35, 45 - 4 * k, 40 + 3 * k]}]}}
            for k in range(n)]


def line_frames(n=6):
    return [{"label": f"t = {k}", "data": {"x": {"label": "Position", "unit": "m"}, "y": {"label": "Height", "unit": "m"},
                                         "series": [{"name": "wave", "points": [[x / 4, round(math.sin(x / 4 - k / 2), 4)]
                                                                                for x in range(41)]}]}}
            for k in range(n)]


def anim(frames, form="chart.bar", **data):
    return S.spec("animation.svg", "Rainfall, year by year", {"form": form, "frames": frames, **data})


@pytest.mark.parametrize("spec_", [anim(bar_frames()), anim(line_frames(), "chart.line", seconds_per_frame=0.5,
                                                                loop=False)])
def test_animation_is_declarative_deterministic_and_carries_every_frame(spec_):
    m = S.made(spec_)
    assert m.bytes == S.made(spec_).bytes
    text = m.bytes.decode()
    assert "<script" not in text and "<animate" not in text and "<set" not in text
    root = S.tree(m.bytes)
    frames = [g for g in root.iter(S.q("g")) if "frame" in S.classes(g)]
    assert len(frames) == len(spec_["data"]["frames"])
    assert [g.get("opacity") for g in frames] == ["0"] * (len(frames) - 1) + [None]   # still picture: last frame
    assert "prefers-reduced-motion:reduce" in text and "animation:none" in text
    assert len(S.groups(root, "axis", "x")) == 1                                  # one set of axes
    v = S.verdict(m.bytes, spec_)
    assert v.ok, [(f.rule, f.detail) for f in v.technical if not f.passed]
    assert S.finding(v, "values_recovered").measure == 1.0


def test_frames_must_share_series():
    frames = bar_frames()
    frames[2]["data"]["series"][1]["name"] = "Bergen"
    with pytest.raises(ValueError, match="same categories and series"):
        S.made(anim(frames))
    frames = bar_frames()
    frames[1]["data"]["series"][0]["values"] = ["wet"] * 4
    with pytest.raises(ValueError, match="does not fit"):
        S.made(anim(frames))


def _first_frame_static(data):
    root = S.tree(data)
    frames = [g for g in root.iter(S.q("g")) if "frame" in S.classes(g)]
    frames[0].attrib.pop("opacity")
    frames[-1].set("opacity", "0")
    return S.to_bytes(root)


def _no_reduced_motion(data):
    return data.replace(b"@media (prefers-reduced-motion:reduce)", b"@media (min-width:1px)")


def _smil(data):
    root = S.tree(data)
    frame = next(g for g in root.iter(S.q("g")) if "frame" in S.classes(g))
    a = S.ET.SubElement(frame, S.q("animate"))
    a.set("attributeName", "opacity")
    return S.to_bytes(root)


def _alter_frame(data):
    root = S.tree(data)
    frame = [g for g in root.iter(S.q("g")) if "frame" in S.classes(g)][1]
    rect = next(r for r in frame.iter(S.q("rect")) if "mark" in S.classes(r))
    rect.set("y", str(float(rect.get("y")) - 30))
    rect.set("height", str(float(rect.get("height")) + 30))
    return S.to_bytes(root)


@pytest.mark.parametrize("mutate,rule", [
    (_first_frame_static, "static_last_frame"),
    (_no_reduced_motion, "reduced_motion_honoured"),
    (_smil, "svg_allowed_elements"),
    (_alter_frame, "values_recovered"),
    (S.add_script, "svg_allowed_elements"),
])
def test_wrong_animations_are_caught(mutate, rule):
    s = anim(bar_frames())
    v = S.verdict(mutate(S.made(s).bytes), s)
    assert not v.ok and rule in S.failed(v), (rule, S.failed(v))


# --- repair -----------------------------------------------------------------------------------------------------

def _make(s):
    return make.make(s)


def _check(m, s):
    return check.check(m.bytes, m.media_type, s)


LONG = {"categories": [f"Region number {i}" for i in range(9)], "x": {"label": "Region"},
        "y": {"label": "Sales", "unit": "units"}, "series": [{"name": "2025", "values": list(range(10, 19))}]}


def test_overlapping_labels_are_rotated_by_a_recorded_rule_and_the_data_is_kept():
    s = S.spec("chart.bar", "Sales by region", copy.deepcopy(LONG), {"x_labels": "horizontal"})
    m = _make(s)
    v = _check(m, s)
    assert not v.ok and "tick_labels_overlap_x" in S.failed(v)
    s2 = repair(s, v)
    assert s2["options"]["x_labels"] == "rotated" and s2["data"] == s["data"]
    assert s2["options"]["history"] == [{"round": 1, "rule": "tick_labels_overlap_x",
                                         "change": "x labels rotated by 45 degrees"}]
    assert spec_mod.validate(s2) == []
    assert _check(_make(s2), s2).ok


def test_more_series_than_styles_become_small_multiples():
    data = {"x": {"label": "Day"}, "y": {"label": "Value"},
            "series": [{"name": f"S{k}", "points": [[d, d * (k + 1)] for d in range(10)]} for k in range(8)]}
    made, verdict, final = repair_loop(S.spec("chart.line", "Eight", data), _make, _check)
    assert final["spec"] == "chart.small_multiples" and final["data"] == data
    assert verdict.ok and S.finding(verdict, "values_recovered").measure == 1.0


def test_more_points_than_pixels_are_aggregated_with_the_rule_stated():
    data = {"x": {"label": "Sample"}, "y": {"label": "Signal", "unit": "mV"},
            "series": [{"name": "trace", "points": [[i, math.sin(i / 40) + (i % 5) / 10] for i in range(4000)]}]}
    made, verdict, final = repair_loop(S.spec("chart.line", "Trace", data), _make, _check)
    assert final["options"]["aggregate"] == {"rule": "min_max", "bins": 160}
    assert "smallest and its largest" in made.bytes.decode()
    assert verdict.ok and S.finding(verdict, "points_exceed_pixels") is None


def test_a_bar_axis_not_from_zero_is_set_back_to_zero():
    data = {"categories": ["a", "b", "c"], "x": {"label": "Item"}, "y": {"label": "Score"},
            "series": [{"name": "s", "values": [80, 85, 96]}]}
    s = S.spec("chart.bar", "Scores", data, {"y_from_zero": False})
    v = check.check(_make(s).bytes, "image/svg+xml", S.spec("chart.bar", "Scores", data))
    s2 = repair(s, v)
    assert s2["options"]["y_from_zero"] is True


def test_repair_stops_after_three_rounds_and_when_nothing_applies():
    s = S.spec("chart.bar", "Sales", copy.deepcopy(LONG),
               {"history": [{"round": r, "rule": "x", "change": "y"} for r in range(1, MAX_ROUNDS + 1)]})
    v = check.Verdict.of((check.Finding("tick_labels_overlap_x", False, "x"),))
    assert repair(s, v) is None
    assert repair(S.spec("chart.bar", "Sales", copy.deepcopy(LONG)),
                  check.Verdict.of((check.Finding("svg_no_href", False, "x"),))) is None


def test_repair_keeps_a_spec_a_spec():
    s = spec_mod.Spec.from_dict(S.spec("chart.bar", "Sales", copy.deepcopy(LONG), {"x_labels": "horizontal"}))
    s2 = repair(s, _check(_make(s), s))
    assert isinstance(s2, spec_mod.Spec) and s2.options["x_labels"] == "rotated" and s2.data == s.data
