#!/usr/bin/env python3
"""Build the NeuroSciFi wordmark from one geometry definition (stdlib only).

The wordmark follows the owner's sketch: NEURO in capitals on one cap height, a large S that holds a small "c" in its
upper-right bay and a small "i" in its lower-left bay (S-ci), and a tall F whose long top bar shelters a small "i"
under its middle bar (F-i). Every glyph is a monoline centre line; the strokes are drawn as a tube (shadow rim, body
and a thin highlight offset towards the upper left) in gray-matter tones.

Outputs (all written from the geometry below; tests/test_brand_wordmark.py checks they are current):
  docs/assets/brand/neuroscifi-wordmark-dark.svg    standalone wordmark for dark backgrounds
  docs/assets/brand/neuroscifi-wordmark-light.svg   standalone wordmark for light backgrounds
  docs/assets/brand/neuroscifi-banner.svg           the wordmark block between the nsf-wordmark markers, and the
                                                    tagline and subtitle offsets (the brain icon is not touched)
  src/neuroscifi/console/web/index.html             inline header wordmark between the nsf-wordmark markers
  src/neuroscifi/pocket/web/index.html              the same
  src/neuroscifi/presence/web/theme.css             the header wordmark's light and dark tokens

Usage:  python scripts/brand/build_wordmark.py           write every output
        python scripts/brand/build_wordmark.py --check   exit 1 when an output is not what the geometry produces
"""
from __future__ import annotations

import argparse
import math
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BRAND = ROOT / "docs" / "assets" / "brand"
BANNER = BRAND / "neuroscifi-banner.svg"
HTML_PAGES = (ROOT / "src/neuroscifi/console/web/index.html", ROOT / "src/neuroscifi/pocket/web/index.html")
THEME_CSS = ROOT / "src/neuroscifi/presence/web/theme.css"
NAME = "NeuroSciFi"

# ---------------------------------------------------------------------------------------------------------------------
# Metrics, in font units. The cap height is 100: cap tops at y=0, the baseline at y=100 (SVG y grows downwards).
# Coordinates below are stroke CENTRE LINES; a straight terminal sits half a stroke inside the outline it reaches.
# ---------------------------------------------------------------------------------------------------------------------
CAP = 100.0


class Cut:
    """An optical size: stroke weights for the capitals, the nested lowercase and the i dots."""

    def __init__(self, name, caps, nest, dot):
        self.name, self.caps, self.nest, self.dot = name, caps, nest, dot


DISPLAY = Cut("display", caps=11.0, nest=8.5, dot=11.0)   # banner and standalone files
SMALL = Cut("small", caps=11.5, nest=9.0, dot=11.5)        # app headers, 24 to 32 px tall

# ---------------------------------------------------------------------------------------------------------------------
# Path utilities
# ---------------------------------------------------------------------------------------------------------------------
_TOK = re.compile(r"[MLHVC]|-?\d*\.?\d+(?:e-?\d+)?")


def parse(d):
    """Path data -> list of subpaths; each subpath is a list of segments ('L', p0, p1) or ('C', p0, c1, c2, p1)."""
    toks = _TOK.findall(d)
    subs, cur, pos, i, cmd = [], None, (0.0, 0.0), 0, None
    while i < len(toks):
        if toks[i] in "MLHVC":
            cmd = toks[i]
            i += 1
        need = {"M": 2, "L": 2, "H": 1, "V": 1, "C": 6}[cmd]
        nums = [float(t) for t in toks[i:i + need]]
        i += need
        if cmd == "M":
            pos = (nums[0], nums[1])
            cur = [("M", pos)]
            subs.append(cur)
            cmd = "L"
        elif cmd in "LHV":
            p1 = (nums[0], nums[1]) if cmd == "L" else (nums[0], pos[1]) if cmd == "H" else (pos[0], nums[0])
            cur.append(("L", pos, p1))
            pos = p1
        else:
            p1 = (nums[4], nums[5])
            cur.append(("C", pos, (nums[0], nums[1]), (nums[2], nums[3]), p1))
            pos = p1
    return subs


def fmt(v):
    s = f"{v:.2f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def emit(subs, dx=0.0, dy=0.0):
    """Subpaths -> compact path data, shifted by (dx, dy); straight segments become H or V where they can."""
    out = []
    for sub in subs:
        for seg in sub:
            if seg[0] == "M":
                out.append(f"M{fmt(seg[1][0] + dx)} {fmt(seg[1][1] + dy)}")
            elif seg[0] == "L":
                (x0, y0), (x1, y1) = seg[1], seg[2]
                if abs(y1 - y0) < 1e-9:
                    out.append(f"H{fmt(x1 + dx)}")
                elif abs(x1 - x0) < 1e-9:
                    out.append(f"V{fmt(y1 + dy)}")
                else:
                    out.append(f"L{fmt(x1 + dx)} {fmt(y1 + dy)}")
            else:
                pts = seg[2:]
                out.append("C" + " ".join(f"{fmt(x + dx)} {fmt(y + dy)}" for x, y in pts))
    return "".join(out).replace(" -", "-")


def sample(subs, n=48):
    pts = []
    for sub in subs:
        for seg in sub:
            if seg[0] == "M":
                pts.append(seg[1])
            elif seg[0] == "L":
                (x0, y0), (x1, y1) = seg[1], seg[2]
                pts += [(x0 + (x1 - x0) * t / n, y0 + (y1 - y0) * t / n) for t in range(1, n + 1)]
            else:
                p0, c1, c2, p1 = seg[1:]
                for k in range(1, n + 1):
                    t = k / n
                    a, b, c, e = (1 - t) ** 3, 3 * (1 - t) ** 2 * t, 3 * (1 - t) * t * t, t ** 3
                    pts.append((a * p0[0] + b * c1[0] + c * c2[0] + e * p1[0],
                                a * p0[1] + b * c1[1] + c * c2[1] + e * p1[1]))
    return pts


# ---------------------------------------------------------------------------------------------------------------------
# Geometry
# ---------------------------------------------------------------------------------------------------------------------
def arc(cx, cy, rx, ry, a0, a1):
    """Elliptical arc as cubic Béziers, angles in degrees (0 = right, 90 = down), at most 90 degrees per piece."""
    n = max(1, math.ceil(abs(a1 - a0) / 90 - 1e-9))
    step = math.radians((a1 - a0) / n)
    k = 4 / 3 * math.tan(step / 4)
    t = math.radians(a0)
    x0, y0 = cx + rx * math.cos(t), cy + ry * math.sin(t)
    out = [f"M{x0:.3f} {y0:.3f}"]
    for _ in range(n):
        t1 = t + step
        x1, y1 = cx + rx * math.cos(t1), cy + ry * math.sin(t1)
        c1 = (x0 - k * rx * math.sin(t), y0 + k * ry * math.cos(t))
        c2 = (x1 + k * rx * math.sin(t1), y1 - k * ry * math.cos(t1))
        out.append(f"C{c1[0]:.3f} {c1[1]:.3f} {c2[0]:.3f} {c2[1]:.3f} {x1:.3f} {y1:.3f}")
        t, x0, y0 = t1, x1, y1
    return " ".join(out)


# The nested lowercase share one x-height: the S's i stem is 27 units tall (outline) and the c 28.5 (round overshoot);
# the F's i is long and stands on the F's foot.
NEST_X = 27.0


def lower_c(cx, cy):
    """A small round c, open to the right, centred at (cx, cy): from the upper terminal over the top to the lower."""
    ry = (NEST_X + 1.5 - DISPLAY.nest) / 2      # 0.75 overshoot above and below the i stems' x-height
    return arc(cx, cy, ry * 0.94, ry, -52, -308)


def lower_i_stem(x, foot):
    """The stem of a small i, NEST_X tall, whose outline stands on y=foot."""
    top = foot - NEST_X + DISPLAY.nest / 2
    return f"M{fmt(x)} {fmt(top)} V{fmt(foot - DISPLAY.nest / 2)}"


# Glyphs: the capital's centre lines, the nested lowercase centre lines and the i dot centres, in glyph-local
# coordinates (x from the glyph's left outline edge). Path data uses M, L, H, V and C only.
GLYPHS = {}


def glyph(name, caps, nest="", dots=()):
    GLYPHS[name] = {"caps": caps, "nest": nest, "dots": tuple(dots)}


glyph("N", "M5 95 V5 L51 95 V5")
glyph("E", "M38 5 H5 V95 H40 M5 48 H26")
glyph("U", "M5 5 V70 C5 87 13.5 96.5 24 96.5 C34.5 96.5 43 87 43 70 V5")
glyph("R", "M5 95 V5 H23 C35.5 5 43 14 43 27.5 C43 41 35.5 50 23 50 H5 "
               "M23 50 C35 52 45 63 49 79 C51.5 89 52 98.5 56 101.5 C59 103.8 62.5 102.6 64 99.5")
glyph("O", "M26 3 C37.5 3 47 22 47 55 C47 80 38 97 26 97 C14 97 5 80 5 55 C5 22 14.5 3 26 3")
glyph("S", "M71 7 C65 -4 53 -11 40 -11 C21 -11 8 -1 8 15 C8 33 24 41 40 47.5 C57 54.5 73 63 73 83 "
           "C73 101 59 111 39 111 C23 111 10 105 5 94",
      nest=lower_c(52, 25) + " " + lower_i_stem(33, 100), dots=((33, 62),))
# F: the top bar runs on to twice the middle bar; the long i hangs under the middle bar's end, down to the F's foot.
glyph("F", "M5 119 V-19 H88 M5 48 H44", nest="M44 84 V120", dots=((44, 69),))

ORDER = "NEUROSF"
# Left edge of each glyph's box (tuned by eye and by the gap report of --report).
POSITIONS = {"N": 0, "E": 76, "U": 132, "R": 200, "O": 266, "S": 326, "F": 418}


def layout():
    """Placed centre lines: (caps, nest, dots) path data in wordmark coordinates (x from 0, cap top at y=0)."""
    caps, nest, dots = [], [], []
    for ch in ORDER:
        g, x = GLYPHS[ch], POSITIONS[ch]
        caps.append(emit(parse(g["caps"]), x))
        if g["nest"]:
            nest.append(emit(parse(g["nest"]), x))
        for cx, cy in g["dots"]:
            dots.append(f"M{fmt(cx + x)} {fmt(cy - 0.01)}V{fmt(cy + 0.01)}")
    return "".join(caps), "".join(nest), "".join(dots)


def bounds(cut):
    """Outline bounds (x0, y0, x1, y1) of the whole wordmark for a cut."""
    caps, nest, dots = layout()
    xs, ys = [], []
    for d, w in ((caps, cut.caps), (nest, cut.nest), (dots, cut.dot)):
        for x, y in sample(parse(d)):
            xs += [x - w / 2, x + w / 2]
            ys += [y - w / 2, y + w / 2]
    return min(xs), min(ys), max(xs), max(ys)


def gaps(cut=DISPLAY):
    """Smallest outline gap between neighbouring glyphs and between each nested letter and its host."""
    def pts(ch, part):
        g, x = GLYPHS[ch], POSITIONS[ch]
        if part == "caps":
            return [(px + x, py) for px, py in sample(parse(g["caps"]), 64)], cut.caps
        if part == "nest":
            return [(px + x, py) for px, py in sample(parse(g["nest"]), 64)], cut.nest
        return [(cx + x, cy) for cx, cy in g["dots"]], cut.dot

    def gap(a, b):
        (pa, wa), (pb, wb) = a, b
        return min(math.dist(p, q) for p in pa for q in pb) - (wa + wb) / 2

    report = {}
    for left, right in zip(ORDER, ORDER[1:]):
        report[left + right] = gap(pts(left, "caps"), pts(right, "caps"))
    for ch in "SF":
        report[ch + " host-nest"] = gap(pts(ch, "caps"), pts(ch, "nest"))
        report[ch + " host-dot"] = gap(pts(ch, "caps"), pts(ch, "dots"))
        report[ch + " nest-dot"] = gap(pts(ch, "nest"), pts(ch, "dots"))
    return report


# ---------------------------------------------------------------------------------------------------------------------
# Palette: gray matter. Warm greys with a pink, mauve and taupe cast; the nested c and i are a rosier gray-matter tone.
# Each role is a tube: rim (the shadow edge, the outline the eye reads), body (two-stop tonal gradient along the
# strokes, lighter at the upper left) and highlight (a thin inner line offset towards the light).
# ---------------------------------------------------------------------------------------------------------------------
PALETTES = {
    "dark": {   # on dark backgrounds (README banner navy #070A1F .. #1A0F3D, app dark #070908 / #101413)
        "rim": ("#A08C93", "#927E86"), "body": ("#DCCBC8", "#BBA3A6"), "hi": "#F4EAE7",
        "nest_rim": ("#B0808D", "#A57683"), "nest_body": ("#E8B9C0", "#D29EA8"), "nest_hi": "#FBE7EA",
    },
    "light": {  # on light backgrounds (Pocket #f4f7f5, console header #fff)
        "rim": ("#4F3F45", "#42343A"), "body": ("#6F5D63", "#57464C"), "hi": "#8D7A80",
        "nest_rim": ("#6A3445", "#5A2B3A"), "nest_body": ("#9A5A6B", "#7F4555"), "nest_hi": "#B37C89",
    },
}


def _hex_lum(h):
    r, g, b = (int(h[i:i + 2], 16) / 255 for i in (1, 3, 5))
    lin = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in (r, g, b)]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def contrast(a, b):
    la, lb = sorted((_hex_lum(a), _hex_lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


# ---------------------------------------------------------------------------------------------------------------------
# SVG assembly
# ---------------------------------------------------------------------------------------------------------------------
# Tube layers: (palette key, width factor, offset factor towards the upper left). Factors are of the stroke width.
LAYERS = (("rim", 1.0, 0.0), ("body", 0.72, 0.10), ("hi", 0.2, 0.24))
LIGHT = (-0.62, -0.78)   # unit direction of the highlight offset (light from the upper left)


def _gradient(gid, stops, box, attrs):
    x0, y0, x1, y1 = box   # mostly vertical: lighter at the top, deeper at the foot, drifting right
    x1 = x0 + (y1 - y0) * 0.4
    out = (f'<linearGradient id="{gid}" gradientUnits="userSpaceOnUse" x1="{fmt(x0)}" y1="{fmt(y0)}" '
           f'x2="{fmt(x1)}" y2="{fmt(y1)}">')
    for off, col in zip((0, 1), stops):
        out += f'<stop offset="{off}" {attrs(col)}/>'
    return out + "</linearGradient>"


def wordmark_body(cut, pid, colours=None, css=False):
    """The <defs> and layered <use> elements of the wordmark, in wordmark units.

    colours: a palette (attributes carry the hex values) or None with css=True (classes carry CSS custom properties).
    pid: id prefix, unique per document."""
    caps, nest, dots = layout()
    x0, y0, x1, y1 = bounds(cut)
    box = (x0, y0, x1, y1)
    defs = [f'<path id="{pid}-caps" d="{caps}"/>', f'<path id="{pid}-nest" d="{nest}"/>',
            f'<path id="{pid}-dots" d="{dots}"/>']
    grads = []
    for key in ("rim", "body", "nest_rim", "nest_body"):
        if css:
            cls = "wm-" + key.replace("_", "-")
            grads.append(_gradient(f"{pid}-{key}", (f"{cls}0", f"{cls}1"), box, lambda c: f'class="{c}"'))
        else:
            grads.append(_gradient(f"{pid}-{key}", colours[key], box, lambda c: f'stop-color="{c}"'))
    uses = []
    for part, width, prefix in (("caps", cut.caps, ""), ("nest", cut.nest, "nest_"), ("dots", cut.dot, "nest_")):
        for role, wf, of in LAYERS:
            key = prefix + role
            w = width * wf
            dx, dy = LIGHT[0] * width * of, LIGHT[1] * width * of
            tr = f' transform="translate({fmt(dx)} {fmt(dy)})"' if of else ""
            if role == "hi":
                paint = f'class="wm-{key.replace("_", "-")}"' if css else f'stroke="{colours[key]}"'
            else:
                paint = f'stroke="url(#{pid}-{key})"'
            uses.append(f'<use href="#{pid}-{part}" {paint} stroke-width="{fmt(w)}"{tr}/>')
    return ("<defs>" + "".join(grads) + "".join(defs) + "</defs>"
            + '<g fill="none" stroke-linecap="round" stroke-linejoin="round">' + "".join(uses) + "</g>")


def view_box(cut, pad):
    x0, y0, x1, y1 = bounds(cut)
    return (math.floor(x0 - pad), math.floor(y0 - pad), math.ceil(x1 + pad) - math.floor(x0 - pad),
            math.ceil(y1 + pad) - math.floor(y0 - pad))


def standalone(variant):
    """A self-contained wordmark SVG (transparent background) for dark or light backgrounds."""
    vx, vy, vw, vh = view_box(DISPLAY, 6)
    k = 2
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{vx} {vy} {vw} {vh}" width="{vw * k}" '
            f'height="{vh * k}" role="img" aria-labelledby="nsfwm-title">\n'
            f'<title id="nsfwm-title">{NAME}</title>\n'
            f'<!-- Generated by scripts/brand/build_wordmark.py ({variant} backgrounds); do not edit by hand. -->\n'
            + wordmark_body(DISPLAY, "nsfwm", PALETTES[variant]) + "\n</svg>\n")


# Banner layout (1280 x 400; the icon is centred at 250,200). The wordmark's cap height in banner pixels and its
# top-left; the tagline and subtitle keep their outlines and move by these offsets.
BANNER_CAP = 96.0
BANNER_LEFT = 476.0
BANNER_CAP_TOP = 146.0
TAGLINE_DY = 1
SUBTITLE_DY = 39


def banner_block():
    k = BANNER_CAP / CAP
    x0 = bounds(DISPLAY)[0]
    tx = BANNER_LEFT - x0 * k
    return (f'<!-- nsf-wordmark:begin (generated by scripts/brand/build_wordmark.py; do not edit by hand) -->\n'
            f'<g id="nsf-wordmark" transform="translate({fmt(tx)} {fmt(BANNER_CAP_TOP)}) scale({fmt(k)})">'
            + wordmark_body(DISPLAY, "nsfwm", PALETTES["dark"]) + "</g>\n<!-- nsf-wordmark:end -->")


def build_banner(text):
    out, n = re.subn(r"<!-- nsf-wordmark:begin.*?<!-- nsf-wordmark:end -->", lambda m: banner_block(), text,
                     flags=re.S)
    if n != 1:
        raise SystemExit("banner: the nsf-wordmark markers are missing")
    for gid, dy in (("nsf-tagline", TAGLINE_DY), ("nsf-subtitle", SUBTITLE_DY)):
        out, n = re.subn(rf'(<g id="{gid}" transform=")[^"]*(")', rf"\g<1>translate(0 {dy})\g<2>", out)
        if n != 1:
            raise SystemExit(f"banner: group {gid} is missing")
    return out


def html_block():
    vx, vy, vw, vh = view_box(SMALL, 1)
    return ('<!-- nsf-wordmark:begin (generated by scripts/brand/build_wordmark.py; do not edit by hand) -->'
            f'<svg class="nsf-wm" viewBox="{vx} {vy} {vw} {vh}" role="img" aria-label="{NAME}" focusable="false">'
            + wordmark_body(SMALL, "nsfwm", css=True) + "</svg><!-- nsf-wordmark:end -->")


def build_html(text):
    out, n = re.subn(r"<!-- nsf-wordmark:begin.*?<!-- nsf-wordmark:end -->", lambda m: html_block(), text, flags=re.S)
    if n != 1:
        raise SystemExit("html: the nsf-wordmark markers are missing")
    return out


def css_block():
    def tokens(p):
        vals = []
        for key in ("rim", "body", "nest_rim", "nest_body"):
            vals += [f"--wm-{key.replace('_', '-')}0:{p[key][0]}", f"--wm-{key.replace('_', '-')}1:{p[key][1]}"]
        vals += [f"--wm-hi:{p['hi']}", f"--wm-nest-hi:{p['nest_hi']}"]
        return ";".join(vals)

    vx, vy, vw, vh = view_box(SMALL, 1)
    rules = [f".nsf-wm{{{tokens(PALETTES['light'])}}}",
             f'[data-theme="dark"] .nsf-wm{{{tokens(PALETTES["dark"])}}}',
             # --wm-h: header height; --wm-descent: the share of it below the cap baseline (S, R, F and i descend)
             f":root{{--wm-h:32px;--wm-descent:{(vy + vh - CAP) / vh:.4f}}}",
             "@media (max-width:480px){:root{--wm-h:28px}}",
             f".nsf-wm{{display:block;height:var(--wm-h);width:auto;aspect-ratio:{vw}/{vh};overflow:visible}}"]
    for key in ("rim", "body", "nest_rim", "nest_body"):
        css = key.replace("_", "-")
        rules.append(f".nsf-wm .wm-{css}0{{stop-color:var(--wm-{css}0)}}.nsf-wm .wm-{css}1{{stop-color:var(--wm-{css}1)}}")
    rules.append(".nsf-wm .wm-hi{stroke:var(--wm-hi)}.nsf-wm .wm-nest-hi{stroke:var(--wm-nest-hi)}")
    return ("/* nsf-wordmark:begin (generated by scripts/brand/build_wordmark.py; do not edit by hand) */\n"
            "/* ---- header wordmark: gray-matter tones, light by default, dark under data-theme=\"dark\" ---- */\n"
            + "\n".join(rules) + "\n/* nsf-wordmark:end */")


def build_css(text):
    block = css_block()
    if "/* nsf-wordmark:begin" not in text:
        return text.rstrip("\n") + "\n\n" + block + "\n"
    return re.sub(r"/\* nsf-wordmark:begin.*?/\* nsf-wordmark:end \*/", lambda m: block, text, flags=re.S)


def outputs():
    """Every generated file: path -> expected content."""
    files = {BRAND / "neuroscifi-wordmark-dark.svg": standalone("dark"),
             BRAND / "neuroscifi-wordmark-light.svg": standalone("light"),
             BANNER: build_banner(BANNER.read_text()),
             THEME_CSS: build_css(THEME_CSS.read_text())}
    for page in HTML_PAGES:
        files[page] = build_html(page.read_text())
    return files


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true", help="report stale outputs and exit 1 instead of writing")
    ap.add_argument("--report", action="store_true", help="print glyph gaps and palette contrast")
    args = ap.parse_args(argv)
    if args.report:
        for k, v in gaps().items():
            print(f"gap {k:14s} {v:6.2f}")
        print("bounds", [round(v, 2) for v in bounds(DISPLAY)])
        return 0
    stale = []
    for path, text in outputs().items():
        current = path.read_text() if path.exists() else None
        if current != text:
            stale.append(path)
            if not args.check:
                path.write_text(text)
    for path in stale:
        print(("stale: " if args.check else "wrote: ") + str(path.relative_to(ROOT)))
    return 1 if args.check and stale else 0


if __name__ == "__main__":
    sys.exit(main())
