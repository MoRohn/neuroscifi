"""The owner's colour rule, enforced (OMNIMIND lane U, ADR-079; the outputs audit found no test for it): the console and
what NeuroSciFi makes are grayscale; a hue appears only on warning and danger, on the Presence entity's own flairs and
in the brand wordmark's fixed tints, and green nowhere but the entity's mint flair. Every colour literal in the
stylesheets the console loads, in its scripts and page, in the made artifacts' stylesheet and in the made pictures'
palette is checked against that, declaration by declaration."""
import colorsys
import re
from pathlib import Path

import pytest

from neuroscifi.artifacts.make import svg_base

SRC = Path(__file__).resolve().parents[2] / "src" / "neuroscifi"
FILES = ("console/web/console.css", "console/web/console.js", "console/web/index.html",
         "console/web/artifacts.mjs", "presence/web/ui.css", "presence/web/theme.css", "voice/web/voice.css",
         "artifacts/web/explorable.css")
GRAY = 16                        # the largest spread of R, G and B a gray may have (the console's grays are warm: 2-9)
# where a hue is allowed: the warning and danger tokens (and their fallbacks), the entity's five flair tones, the
# brand wordmark's own tints
HUE_NAMES = re.compile(r"^--(warn|danger|rust)(-[a-z]+)?$|^--pc-(purple|blue|mint|yellow|pink)$|^--wm-")
HUE_FALLBACK = re.compile(r"var\(--(warn|danger|rust)[a-z-]*\s*,\s*$")
FLAIRS = re.compile(r"^--pc-(purple|blue|mint|yellow|pink)$")
HEX = re.compile(r"#([0-9a-fA-F]{6}|[0-9a-fA-F]{3})(?![0-9a-zA-Z_-])")
RGB = re.compile(r"rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)")
NAMED = re.compile(r"(?<![\w-])(green|lime|olive|teal|aqua|cyan|blue|navy|purple|fuchsia|magenta|red|maroon|orange|"
                   r"yellow|pink|brown|rebeccapurple)(?![\w-])", re.I)


def colours(text):
    """(start, (r, g, b)) of every hex or rgb() literal in `text`."""
    for m in HEX.finditer(text):
        h = m.group(1)
        h = "".join(c * 2 for c in h) if len(h) == 3 else h
        yield m.start(), tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
    for m in RGB.finditer(text):
        yield m.start(), tuple(int(x) for x in m.groups())


def is_green(rgb) -> bool:
    hue, _, _ = colorsys.rgb_to_hsv(*(c / 255 for c in rgb))
    return max(rgb) - min(rgb) > GRAY and 70 <= hue * 360 <= 170


def declarations(css):
    """(selector, property, value) of every declaration; the innermost block is the selector (an @media block's
    rules are found inside it)."""
    css = re.sub(r"/\*.*?\*/", " ", css, flags=re.S)
    for m in re.finditer(r"([^{}]*)\{([^{}]*)\}", css):
        selector = " ".join(m.group(1).split())
        for decl in m.group(2).split(";"):
            name, sep, value = decl.partition(":")
            if sep:
                yield selector, name.strip(), value


def problems_css(css: str, where: str) -> list[str]:
    out = []
    for selector, name, value in declarations(css):
        for start, rgb in colours(value):
            if max(rgb) - min(rgb) <= GRAY:
                continue
            if is_green(rgb) and not FLAIRS.match(name):
                out.append(f"{where}: green {rgb} in {selector} {{{name}}}")
            elif not (HUE_NAMES.match(name) or HUE_FALLBACK.search(value[:start])):
                out.append(f"{where}: hue {rgb} outside warning, danger and the entity's flairs: {selector} {{{name}}}")
        if NAMED.search(value) and name not in ("content", "font-family"):
            out.append(f"{where}: a named colour in {selector} {{{name}: {value.strip()[:60]}}}")
    return out


def problems_script(text: str, where: str) -> list[str]:
    """Scripts and the page may not paint with a literal colour at all: they use the stylesheets' tokens."""
    text = re.sub(r"//[^\n]*", " ", text)
    return [f"{where}: colour literal {rgb}" for _, rgb in colours(text) if max(rgb) - min(rgb) > GRAY]


@pytest.mark.parametrize("name", FILES)
def test_the_console_and_made_artifact_stylesheets_keep_the_colour_rule(name):
    path = SRC / name
    text = path.read_text(encoding="utf-8")
    found = problems_css(text, name) if name.endswith(".css") else problems_script(text, name)
    assert found == []


def test_the_made_pictures_palette_is_gray_but_for_warning_and_danger():
    for palette in (svg_base.LIGHT, svg_base.DARK):
        for key, value in palette.items():
            (_, rgb), = colours(value)
            if key in ("warn", "danger"):
                assert not is_green(rgb)
            else:
                assert max(rgb) - min(rgb) <= GRAY, (key, value)


def test_the_rule_catches_green_and_hue_where_none_is_allowed():
    """The checker itself fails on what it exists to catch (so a green it misses cannot pass by accident)."""
    assert problems_css(".card { color: #2e8b57; }", "x")                     # green text
    assert problems_css(".chart { fill: rgb(29, 95, 163); }", "x")            # the old heat map's blue
    assert problems_css(".chart { fill: green; }", "x")                       # a named colour
    assert problems_css(":root { --warn: #3ddd52; }", "x")                    # green even on a warning
    assert problems_css("@media (x) { .a { --ok: #3DDDB2; } }", "x")          # mint outside the entity
    assert problems_script("const BLUE = '#1d5fa3';", "x")
    assert not problems_css(":root { --warn: #8A3A14; --pc-mint: #3DDDB2; --ink: #1D191B; }", "x")
    assert not problems_css(".b { color: var(--rust, #a33); }", "x")
