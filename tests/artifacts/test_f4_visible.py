"""Fix lane F4, item 2 (measurement review, second pass, R2-1 and R2-2): the product's check sees only what a person
would see. Each hiding below is the reviewer's probe (`review1/m/r2/probe_hide.py`) or one the brief names, applied to
the product's own line, bar and scatter charts (made from the fixed golden specifications, not from any benchmark
case); the check must say `ok` False with the finding `visible_to_a_person` failed, and the untouched chart must pass."""
import re

import pytest

from g1_golden_specs import SPECS
from neuroscifi.artifacts import check, make
from neuroscifi.artifacts.types import Limits


def _sub1(pattern, repl, text):
    return re.sub(pattern, repl, text, count=1)


def _before_close(text, extra):
    i = text.rfind("</svg>")
    return text[:i] + extra + text[i:]


def _marks(text, extra):
    return re.sub(r'class="(mark|series)([^"]*)"', r'class="\1\2" ' + extra, text)


def _repaint(fill, stroke):
    """Every mark and series painted with these (its own fill and stroke replaced, so the XML stays well formed)."""
    def tag(m):
        t = re.sub(r' (fill|stroke)="[^"]*"', "", m.group(0))
        return t[:-2] + f' fill="{fill}" stroke="{stroke}"/>' if t.endswith("/>") else t[:-1] + f' fill="{fill}" stroke="{stroke}">'
    return lambda t: re.sub(r'<(?:circle|rect|polyline|path|polygon)\b[^>]*class="(?:mark|series)[^"]*"[^>]*>', tag, t)


def _style(css):
    return lambda t: _sub1(r"(<svg[^>]*>)", r"\1<style>" + css + "</style>", t)


HIDINGS = {
    "plot visibility hidden": lambda t: t.replace('class="plot"', 'class="plot" visibility="hidden"', 1),
    "plot display none": lambda t: t.replace('class="plot"', 'class="plot" display="none"', 1),
    "plot opacity 0": lambda t: t.replace('class="plot"', 'class="plot" opacity="0"', 1),
    "style rule .mark,.series display none": _style(".mark,.series{display:none}"),
    "style rule .plot opacity 0": _style(".plot{opacity:0}"),
    "style rule g.plot visibility hidden": _style("g.plot{visibility:hidden}"),
    "plot clipped to nothing": lambda t: _sub1(r"(<svg[^>]*>)", r'\1<defs><clipPath id="z"><rect width="0" height="0"/>'
                                                                 r'</clipPath></defs>', t)
    .replace('class="plot"', 'class="plot" clip-path="url(#z)"', 1),
    "tick labels font-size 0": _style(".tick text{font-size:0}"),
    "marks fill and stroke none": _repaint("none", "none"),
    "marks painted in the background's white": _repaint("#ffffff", "#ffffff"),
    "marks painted with the paper's own colour": _repaint("var(--nsf-paper)", "var(--nsf-paper)"),
    "white rect over everything (occlusion)": lambda t: _before_close(t, '<rect x="0" y="0" width="100%" height="100%" '
                                                                         'fill="#ffffff"/>'),
    "root width 0": lambda t: _sub1(r'<svg([^>]*?) width="[^"]*"', r'<svg\1 width="0"', t),
    "plot moved off the canvas": lambda t: t.replace('class="plot"', 'class="plot" transform="translate(5000,0)"', 1),
    "a rule this check cannot read hides": _style("g[class~=plot]{display:none}"),
}
SCATTER_ONLY = {"circles with no radius": lambda t: _marks(t, 'stroke-width="0"').replace(' r="', ' data-r="')}


@pytest.fixture(scope="module")
def charts():
    out = {}
    for form in ("chart.line", "chart.bar", "chart.scatter"):
        made = make.make(SPECS[form], Limits())
        out[form] = (made.bytes.decode(), made.media_type)
    return out


def _verdict(text, media, form):
    v = check.check(text.encode(), media, SPECS[form])
    return v, next(f for f in v.technical if f.rule == "visible_to_a_person")


@pytest.mark.parametrize("form", ["chart.line", "chart.bar", "chart.scatter"])
def test_the_untouched_chart_passes(charts, form):
    v, seen = _verdict(*charts[form], form)
    assert v.ok and seen.passed


@pytest.mark.parametrize("name", sorted(HIDINGS))
@pytest.mark.parametrize("form", ["chart.line", "chart.bar", "chart.scatter"])
def test_a_hidden_chart_fails(charts, form, name):
    text, media = charts[form]
    hidden = HIDINGS[name](text)
    assert hidden != text
    v, seen = _verdict(hidden, media, form)
    assert not v.ok and not seen.passed, (name, seen.detail)


@pytest.mark.parametrize("name", sorted(SCATTER_ONLY))
def test_a_hidden_scatter_fails(charts, name):
    text, media = charts["chart.scatter"]
    v, seen = _verdict(SCATTER_ONLY[name](text), media, "chart.scatter")
    assert not v.ok and not seen.passed and "radius" in seen.detail


def test_a_harmless_later_shape_and_a_dark_scheme_rule_do_not_fail(charts):
    text, media = charts["chart.line"]
    small = _before_close(text, '<rect x="0" y="0" width="4" height="4" fill="#ffffff"/>')    # covers no mark
    dark = _style("@media (prefers-color-scheme:dark){.tick text{font-size:11px}}")(text)
    for variant in (small, dark):
        v, seen = _verdict(variant, media, "chart.line")
        assert seen.passed, seen.detail
