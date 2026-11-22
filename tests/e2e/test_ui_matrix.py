"""The shared design system (docs/ui/DESIGN_SYSTEM.md) across the size, theme, pointer and forced-colours matrix, in a
real Chromium under the real CSP: no horizontal overflow at 320-2560 px, 44 px targets on coarse pointers, the
presence entity as the hero of the interaction views, WCAG contrast of the colour tokens in both themes, and a
legible forced-colours rendering. Console and Pocket are checked with the same rules (parity)."""
import re

import pytest
from playwright.sync_api import expect

from conftest import pair
from test_console_uat import Uat
from test_pocket_research_e2e import Spark

WIDTHS = {320: 700, 360: 760, 390: 844, 414: 896, 768: 1024, 1024: 1366, 1280: 900, 1440: 960, 1920: 1080, 2560: 1440}
CONSOLE_VIEWS = {"overview": "Overview", "research": "Research", "autonomy": "Autonomy", "knowledge": "Knowledge",
                 "voice": "Voice"}
STAGED = ("research", "voice")   # interaction views: the entity stands on the stage

LAYOUT = r"""() => {
  const W = document.documentElement.clientWidth, out = {scroll: document.documentElement.scrollWidth - W, wide: [], small: []};
  const shown = e => { if (!e.checkVisibility || !e.checkVisibility({visibilityProperty: true})) return false;
    const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  const label = e => e.tagName.toLowerCase() + (e.id ? '#' + e.id : '') + (typeof e.className === 'string' && e.className ? '.' + e.className.trim().split(/\s+/)[0] : '');
  const clippedBy = e => { for (let p = e.parentElement; p; p = p.parentElement) if (/(auto|scroll|hidden|clip)/.test(getComputedStyle(p).overflowX)) return true; return false; };
  for (const e of document.querySelectorAll('main *, header *, body > footer *')) {
    if (!shown(e)) continue;
    const r = e.getBoundingClientRect();
    if ((r.right > W + 1 || r.left < -1) && !clippedBy(e) && out.wide.length < 6) out.wide.push(label(e) + ' ' + Math.round(r.left) + '..' + Math.round(r.right));
  }
  for (const e of document.querySelectorAll('a[href], button, input, select, textarea, summary, [tabindex="0"]')) {
    if (!shown(e) || e.closest('.vh, .pc-vh, .nsfv-vh')) continue;
    const r = e.getBoundingClientRect();
    if (r.width >= 44 && r.height >= 44) continue;
    if (e.matches('input[type=checkbox], input[type=radio]')) { const l = e.closest('label'); if (l && l.getBoundingClientRect().height >= 44) continue; }
    if (e.matches('input[type=file]') && r.height >= 44) continue;
    // WCAG 2.5.8 inline exception: a link inside a sentence
    if (e.tagName === 'A' && getComputedStyle(e).display === 'inline' && e.closest('p, li, dd, small, td')) continue;
    if (out.small.length < 8) out.small.push(label(e) + ' ' + Math.round(r.width) + 'x' + Math.round(r.height));
  }
  const svg = document.querySelector('.nsf-presence svg.pc-svg');
  const box = svg ? svg.getBoundingClientRect() : null;
  out.presence = box ? {w: box.width, left: box.left, right: box.right, place: document.querySelector('.nsf-presence').dataset.place} : null;
  return out;
}"""


def luminance(hex_):
    c = [int(hex_[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    c = [x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4 for x in c]
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]


def contrast(a, b):
    hi, lo = sorted((luminance(a), luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


TEXT_PAIRS = [("ink", s) for s in ("bg", "surface", "surface-muted", "surface-sunken")] + \
    [("ink-muted", s) for s in ("bg", "surface", "surface-muted")] + \
    [("link", "bg"), ("link", "surface"), ("link", "primary-soft"), ("on-primary", "primary"), ("on-primary", "primary-hover"), ("accent", "surface"),
     ("ok", "ok-soft"), ("warn", "warn-soft"), ("danger", "danger-soft"), ("danger", "surface"), ("info", "info-soft"),
     ("neutral", "neutral-soft")]
UI_PAIRS = [("line-strong", "surface"), ("line-strong", "bg"), ("focus", "bg"), ("focus", "surface")]   # 3:1 (1.4.11)


def tokens(page):
    names = sorted({n for pair in TEXT_PAIRS + UI_PAIRS for n in pair})
    values = page.evaluate("names => names.map(n => getComputedStyle(document.documentElement).getPropertyValue('--' + n).trim())", names)
    return dict(zip(names, values))


def check(page, where, coarse, staged):
    m = page.evaluate(LAYOUT)
    assert m["scroll"] <= 0 and not m["wide"], (where, m["scroll"], m["wide"])
    if coarse:
        assert not m["small"], (where, m["small"])
    p = m["presence"]
    assert p and p["left"] >= -1 and p["right"] <= page.viewport_size["width"] + 1, (where, p)
    if staged:
        assert p["place"] == "dock" and p["w"] >= 140, (where, p)


def go(page, view):
    page.locator("#nav").get_by_role("link", name=re.compile("^" + CONSOLE_VIEWS[view])).click()
    expect(page.locator(f"html[data-view='{view}'][data-inflight='0']")).to_have_count(1)
    expect(page.locator(f"#{view} [aria-busy='true']")).to_have_count(0)


@pytest.fixture
def console(tmp_path):
    u = Uat(tmp_path)
    yield u
    u.close()


@pytest.fixture
def spark(tmp_path):
    s = Spark(tmp_path, delay=0.0)
    yield s
    s.close()


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_console_matrix(console, browser, theme):
    for coarse in (True, False):
        ctx = browser.new_context(viewport={"width": 390, "height": 844}, color_scheme=theme,
                                  **({"has_touch": True, "is_mobile": True} if coarse else {}))
        page = ctx.new_page()
        problems = []
        page.on("pageerror", lambda e: problems.append(str(e)))
        page.goto(console.url)
        expect(page.locator("#h-overview")).to_be_visible()
        assert page.evaluate("document.documentElement.dataset.theme") == theme
        for width, height in WIDTHS.items():
            if coarse != (width <= 1024):
                continue
            page.set_viewport_size({"width": width, "height": height})
            for view in CONSOLE_VIEWS:
                go(page, view)
                check(page, f"console {view} {width} {theme}", coarse, view in STAGED)
        assert not problems, problems
        ctx.close()


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_pocket_matrix(spark, browser, theme):
    ctx = browser.new_context(viewport={"width": 390, "height": 844}, color_scheme=theme, has_touch=True, is_mobile=True)
    page = ctx.new_page()
    problems = []
    page.on("pageerror", lambda e: problems.append(str(e)))
    page.goto(spark.url)
    pair(page)
    expect(page.locator("#researchform")).to_be_visible()
    page.locator("#dismiss").click()
    for width, height in WIDTHS.items():
        page.set_viewport_size({"width": width, "height": height})
        check(page, f"pocket {width} {theme}", True, True)
        stage = page.locator("[data-presence-dock]").bounding_box()
        form = page.locator("#researchform").bounding_box()
        assert stage["y"] + stage["height"] <= form["y"] + 1, (width, stage, form)   # the entity above the question
    assert not problems, problems
    ctx.close()


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_colour_tokens_meet_wcag_aa(console, new_page, theme):
    page = new_page(1280, 900, color_scheme=theme)
    page.goto(console.url)
    expect(page.locator("#h-overview")).to_be_visible()
    t = tokens(page)
    assert all(re.fullmatch(r"#[0-9A-Fa-f]{6}", v) for v in t.values()), t
    low = [(fg, bg, round(contrast(t[fg], t[bg]), 2)) for fg, bg in TEXT_PAIRS if contrast(t[fg], t[bg]) < 4.5]
    low += [(fg, bg, round(contrast(t[fg], t[bg]), 2)) for fg, bg in UI_PAIRS if contrast(t[fg], t[bg]) < 3]
    assert not low, low
    # the browser chrome follows the theme
    assert page.locator('meta[name="color-scheme"]').get_attribute("content") == theme
    assert page.locator('meta[name="theme-color"]').get_attribute("content").lower() == t["bg"].lower()


def test_forced_colours_keep_states_legible(console, spark, new_page):
    page = new_page(1280, 900, forced_colors="active")
    page.goto(console.url)
    go(page, "research")
    current = page.locator("#nav a[aria-current='page']")
    assert current.evaluate("e => getComputedStyle(e).outlineStyle") != "none"
    assert page.locator("#askMode").evaluate("e => getComputedStyle(e).appearance") != "none"
    badge = page.locator(".badge").first
    expect(badge).to_be_visible()
    assert badge.evaluate("e => getComputedStyle(e).borderTopStyle") == "solid"
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    phone = new_page(390, 844, forced_colors="active", has_touch=True, is_mobile=True)
    phone.goto(spark.url)
    expect(phone.locator(".nsf-presence svg.pc-svg")).to_be_visible()
    assert phone.locator("section#workspace").evaluate("e => getComputedStyle(e).borderTopStyle") == "solid"
    assert phone.evaluate("document.documentElement.scrollWidth <= innerWidth")
    assert not page.problems and not phone.problems, (page.problems, phone.problems)
