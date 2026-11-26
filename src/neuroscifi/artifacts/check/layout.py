"""Layout, legibility and palette of an SVG picture, judged from its own bytes.

Technical (decides ok) where it destroys reading: text boxes overlapping substantially (more than 15 % of the
smaller box) or leaving the canvas, marks outside the plot area, series that look alike, text below 4.5:1 or marks
below 3:1 contrast against the light or the dark background (WCAG 2), a hue that is not a flagged warning or
danger, any green, a missing <title> or <desc>, a plot without marks. Style otherwise: small overlaps, more ticks
than the axis length carries, more points than pixels.

Text width (stated, independent of any generator): 0.55 em per ASCII character, 1 em per other character; a text
box runs from 0.75 em above the baseline to 0.2 em below it. Colours are resolved from the picture's own CSS (root
rules for light; `@media (prefers-color-scheme: dark)` root rules on top for dark), `var()`, `currentColor`,
inherited fill and stroke, and opacities blended over the background.
"""
from __future__ import annotations

import colorsys
import re

from . import Finding
from .svg_read import Elem, apply, axes_of, canvas, ctm, fnum, mark_geometry, plot_marks

TEXT_EM = 0.55
OVERLAP_TECHNICAL = 0.15
TEXT_CONTRAST = 4.5
MARK_CONTRAST = 3.0
NAMED = {"black": (0, 0, 0), "white": (255, 255, 255), "gray": (128, 128, 128), "grey": (128, 128, 128),
         "silver": (192, 192, 192), "red": (255, 0, 0), "green": (0, 128, 0), "lime": (0, 255, 0),
         "blue": (0, 0, 255), "yellow": (255, 255, 0), "orange": (255, 165, 0), "purple": (128, 0, 128),
         "teal": (0, 128, 128), "navy": (0, 0, 128), "olive": (128, 128, 0), "maroon": (128, 0, 0),
         "aqua": (0, 255, 255), "cyan": (0, 255, 255), "fuchsia": (255, 0, 255), "magenta": (255, 0, 255),
         "darkgreen": (0, 100, 0), "forestgreen": (34, 139, 34), "seagreen": (46, 139, 87),
         "limegreen": (50, 205, 50), "springgreen": (0, 255, 127), "lightgreen": (144, 238, 144)}
PAINT_ATTRS = ("fill", "stroke", "stop-color", "color", "flood-color", "lighting-color")
_HEX = re.compile(r"#([0-9a-fA-F]{3}|[0-9a-fA-F]{6})\b")
_RGB = re.compile(r"rgba?\(\s*([\d.]+)%?\s*[, ]\s*([\d.]+)%?\s*[, ]\s*([\d.]+)%?", re.I)
_VAR = re.compile(r"var\(\s*(--[\w-]+)\s*(?:,\s*([^)]*))?\)")


# --- CSS --------------------------------------------------------------------------------------------------------

def _blocks(css: str):
    """Yield (prelude, body) of top-level blocks; nested at-rule bodies are returned raw."""
    css = re.sub(r"/\*.*?\*/", "", css or "", flags=re.S)
    i, n = 0, len(css)
    while i < n:
        j = css.find("{", i)
        if j < 0:
            return
        depth, k = 1, j + 1
        while k < n and depth:
            if css[k] == "{":
                depth += 1
            elif css[k] == "}":
                depth -= 1
            k += 1
        yield css[i:j].strip(), css[j + 1:k - 1]
        i = k


def _decls(body: str) -> dict[str, str]:
    out = {}
    for part in body.split(";"):
        if ":" in part:
            k, v = part.split(":", 1)
            out[k.strip().lower() if not k.strip().startswith("--") else k.strip()] = v.strip()
    return out


def css_rules(css: str):
    """(media or None, selector, declarations) for every rule; keyframes are skipped."""
    out = []
    for prelude, body in _blocks(css):
        low = prelude.lower()
        if low.startswith("@media"):
            for sel, inner in _blocks(body):
                out.append((low, sel, _decls(inner)))
        elif low.startswith("@"):
            continue
        else:
            out.append((None, prelude, _decls(body)))
    return out


def _matches_root(selector: str, root: Elem) -> bool:
    rid, rclasses = root.attrs.get("id"), root.classes()
    for sel in selector.split(","):
        sel = sel.strip()
        if sel in ("svg", ":root", "*") or (rid and sel in (f"#{rid}", f"svg#{rid}")) or \
                (sel.startswith(".") and sel[1:] in rclasses):
            return True
    return False


def root_styles(root: Elem) -> dict[str, dict[str, str]]:
    """{'light': decls, 'dark': decls} that apply to the root element."""
    css = "".join(n.text for n in root.iter() if n.tag == "style")
    light: dict[str, str] = {}
    dark: dict[str, str] = {}
    rules = css_rules(css)
    for media, sel, decls in rules:
        if media is None and _matches_root(sel, root):
            light.update(decls)
            dark.update(decls)
    for media, sel, decls in rules:
        if media and "prefers-color-scheme" in media and _matches_root(sel, root):
            (dark if "dark" in media else light).update(decls)
    return {"light": light, "dark": dark}


# --- colours ----------------------------------------------------------------------------------------------------

def parse_color(text: str | None):
    """(r, g, b) or None for none/transparent/unknown."""
    if not text:
        return None
    t = text.strip().lower()
    m = _HEX.fullmatch(t) if t.startswith("#") else None
    if m:
        h = m.group(1)
        if len(h) == 3:
            h = "".join(c * 2 for c in h)
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
    m = _RGB.match(t)
    if m:
        vals = [float(v) for v in m.groups()]
        if "%" in t:
            vals = [v * 2.55 for v in vals]
        return tuple(max(0, min(255, round(v))) for v in vals)
    return NAMED.get(t)


def resolve(value: str | None, styles: dict[str, str], depth: int = 0) -> str | None:
    if value is None or depth > 6:
        return value
    m = _VAR.search(value)
    if not m:
        return value
    name, fallback = m.group(1), m.group(2)
    rep = styles.get(name, fallback)
    return resolve(value[:m.start()] + (rep or "") + value[m.end():], styles, depth + 1)


def is_gray(rgb) -> bool:
    return max(rgb) - min(rgb) <= 3


def is_green(rgb) -> bool:
    h, s, v = colorsys.rgb_to_hsv(*(c / 255 for c in rgb))
    return 70 <= h * 360 <= 170 and s >= 0.25 and v >= 0.15


def luminance(rgb) -> float:
    def ch(c):
        c = c / 255
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (ch(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def ratio(a, b) -> float:
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def mix(fg, bg, alpha):
    return tuple(round(f * alpha + b * (1 - alpha)) for f, b in zip(fg, bg))


class Colours:
    def __init__(self, root: Elem):
        self.root = root
        self.styles = root_styles(root)
        self.paper = {}
        for mode in ("light", "dark"):
            st = self.styles[mode]
            bg = resolve(st.get("background-color") or st.get("background"), st)
            rgb = parse_color(bg)
            if rgb is None:
                for n in root.children:
                    if n.tag == "rect" and "paper" in n.classes():
                        rgb = parse_color(resolve(n.attrs.get("fill"), st))
            self.paper[mode] = rgb or ((255, 255, 255) if mode == "light" else (0, 0, 0))

    def current(self, node: Elem, mode: str):
        st = self.styles[mode]
        for n in [node, *node.ancestors()]:
            if "color" in n.attrs:
                return parse_color(resolve(n.attrs["color"], st))
        return parse_color(resolve(st.get("color"), st)) or (0, 0, 0)

    def paint(self, node: Elem, prop: str, mode: str):
        """Resolved (r, g, b, alpha) of `prop` ('fill' or 'stroke') on `node`, or None."""
        st = self.styles[mode]
        value = None
        for n in [node, *node.ancestors()]:
            if prop in n.attrs:
                value = n.attrs[prop]
                break
        if value is None:
            value = "black" if prop == "fill" else "none"
        value = resolve(value, st)
        if value is None or value.strip().lower() in ("none", "transparent"):
            return None
        if value.strip().lower() == "currentcolor":
            rgb = self.current(node, mode)
        elif value.strip().startswith("url("):
            return None
        else:
            rgb = parse_color(value)
        if rgb is None:
            return None
        alpha = 1.0
        for n in [node, *node.ancestors()]:
            alpha *= max(0.0, min(1.0, fnum(n.attrs.get("opacity"), 1.0)))
        for n in [node, *node.ancestors()]:
            if f"{prop}-opacity" in n.attrs:
                alpha *= max(0.0, min(1.0, fnum(n.attrs[f"{prop}-opacity"], 1.0)))
                break
        return rgb + (alpha,)

    def over(self, paint, mode, bg=None):
        bg = bg or self.paper[mode]
        return mix(paint[:3], bg, paint[3])


# --- text boxes -------------------------------------------------------------------------------------------------

def _font_size(node: Elem) -> float:
    for n in [node, *node.ancestors()]:
        if "font-size" in n.attrs:
            return fnum(n.attrs["font-size"], 16)
    return 16.0


def _anchor(node: Elem) -> str:
    for n in [node, *node.ancestors()]:
        if "text-anchor" in n.attrs:
            return n.attrs["text-anchor"]
    return "start"


def text_box(node: Elem):
    """The four corners (root space) of a <text>'s estimated box, its label, and its size."""
    label = node.label()
    size = _font_size(node)
    width = sum(TEXT_EM if ord(c) < 128 else 1.0 for c in label) * size
    x, y = fnum(node.attrs.get("x")), fnum(node.attrs.get("y"))
    a = _anchor(node)
    x0 = x if a == "start" else x - width / 2 if a == "middle" else x - width
    base = node.attrs.get("dominant-baseline", "")
    if base in ("middle", "central"):
        y0, y1 = y - size * 0.5, y + size * 0.5
    elif base == "hanging":
        y0, y1 = y, y + size
    else:
        y0, y1 = y - size * 0.75, y + size * 0.2
    m = ctm(node)
    return [apply(m, px, py) for px, py in ((x0, y0), (x0 + width, y0), (x0 + width, y1), (x0, y1))], label, size


def _area(poly):
    return abs(sum(poly[i][0] * poly[(i + 1) % len(poly)][1] - poly[(i + 1) % len(poly)][0] * poly[i][1]
                   for i in range(len(poly)))) / 2


def _clip(subject, clipper):
    """Sutherland-Hodgman: the intersection of two convex polygons (clipper counter-clockwise or clockwise)."""
    def inside(p, a, b, sign):
        return sign * ((b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0])) >= 0

    def cross(p, q, a, b):
        dx1, dy1 = q[0] - p[0], q[1] - p[1]
        dx2, dy2 = b[0] - a[0], b[1] - a[1]
        den = dx1 * dy2 - dy1 * dx2
        if den == 0:
            return p
        t = ((a[0] - p[0]) * dy2 - (a[1] - p[1]) * dx2) / den
        return p[0] + t * dx1, p[1] + t * dy1
    cs = sum(clipper[i][0] * clipper[(i + 1) % 4][1] - clipper[(i + 1) % 4][0] * clipper[i][1] for i in range(4))
    sign = 1 if cs > 0 else -1
    out = subject
    for i in range(len(clipper)):
        a, b = clipper[i], clipper[(i + 1) % len(clipper)]
        inp, out = out, []
        if not inp:
            break
        s = inp[-1]
        for e in inp:
            if inside(e, a, b, sign):
                if not inside(s, a, b, sign):
                    out.append(cross(s, e, a, b))
                out.append(e)
            elif inside(s, a, b, sign):
                out.append(cross(s, e, a, b))
            s = e
    return out


def _bbox(poly):
    xs, ys = [p[0] for p in poly], [p[1] for p in poly]
    return min(xs), min(ys), max(xs), max(ys)


def _axis_of(node: Elem) -> str | None:
    for n in node.ancestors():
        c = n.classes()
        if "axis" in c:
            for w in ("x", "y", "z"):
                if w in c:
                    return w
    return None


def _in_tick(node: Elem) -> bool:
    return any("tick" in n.classes() for n in node.ancestors())


def visible_texts(root: Elem):
    out = []
    for n in root.iter():
        if n.tag == "text" and n.label():
            if any(a.tag in ("defs", "pattern", "clipPath", "metadata") for a in n.ancestors()):
                continue
            out.append(n)
    return out


# --- the checks -------------------------------------------------------------------------------------------------

def check_layout(root: Elem) -> tuple[list[Finding], list[Finding]]:
    technical: list[Finding] = []
    style: list[Finding] = []
    cx0, cy0, cx1, cy1 = canvas(root)
    texts = visible_texts(root)
    boxes = [(text_box(t), t) for t in texts]

    # Title and description.
    title = next((c for c in root.children if c.tag == "title"), None)
    desc = next((c for c in root.children if c.tag == "desc"), None)
    ok = bool(title is not None and title.text.strip() and desc is not None and desc.text.strip())
    technical.append(Finding("title_and_description", ok, "the picture has a <title> and a <desc>" if ok else
                             "the picture lacks a non-empty <title> or <desc> (screen readers get nothing)"))

    # Text inside the canvas.
    outside = [lab for (poly, lab, _s), _t in boxes
               if any(p[0] < cx0 - 1 or p[0] > cx1 + 1 or p[1] < cy0 - 1 or p[1] > cy1 + 1 for p in poly)]
    technical.append(Finding("text_inside_canvas", not outside,
                             "every text lies inside the canvas" if not outside else
                             f"{len(outside)} text(s) run off the canvas (clipped)", float(len(outside))))

    # Overlaps (bucketed by bounding box).
    grid: dict[tuple[int, int], list[int]] = {}
    bbs = [_bbox(poly) for (poly, _l, _s), _t in boxes]
    for i, (a, b, c, d) in enumerate(bbs):
        for gx in range(int(a // 64), int(c // 64) + 1):
            for gy in range(int(b // 64), int(d // 64) + 1):
                grid.setdefault((gx, gy), []).append(i)
    seen = set()
    worst: dict[str, float] = {}
    counts: dict[str, int] = {}
    for cell in grid.values():
        for ii in range(len(cell)):
            for jj in range(ii + 1, len(cell)):
                i, j = cell[ii], cell[jj]
                if (i, j) in seen:
                    continue
                seen.add((i, j))
                a, b = bbs[i], bbs[j]
                if a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1]:
                    continue
                pi, pj = boxes[i][0][0], boxes[j][0][0]
                inter = _clip(pi, pj)
                area = _area(inter) if len(inter) >= 3 else 0.0
                if area <= 0.5:
                    continue
                share = area / max(1e-9, min(_area(pi), _area(pj)))
                ti, tj = boxes[i][1], boxes[j][1]
                ai, aj = _axis_of(ti), _axis_of(tj)
                rule = f"tick_labels_overlap_{ai}" if ai and ai == aj and _in_tick(ti) and _in_tick(tj) else "text_overlap"
                worst[rule] = max(worst.get(rule, 0.0), share)
                counts[rule] = counts.get(rule, 0) + 1
    for rule in ("tick_labels_overlap_x", "tick_labels_overlap_y", "text_overlap"):
        share = worst.get(rule, 0.0)
        if share > OVERLAP_TECHNICAL:
            technical.append(Finding(rule, False, f"{counts[rule]} pair(s) of texts overlap (worst {share:.0%} of the "
                                                  "smaller box)", share))
        elif share > 0:
            style.append(Finding(rule, False, f"{counts[rule]} pair(s) of texts touch (worst {share:.0%})", share))
        elif rule == "text_overlap":
            technical.append(Finding("text_overlap", True, "no texts overlap", 0.0))

    # Plots: marks inside, distinguishable series, density, tick counts.
    scopes = [n for n in root.iter() if n.tag == "g" and "panel" in n.classes()] or [root]
    n_marks = 0
    plots_seen = 0
    inside_bad = 0
    alike: list[str] = []
    for scope in scopes:
        axes = axes_of(scope)
        plots = [n for n in scope.iter() if n.tag == "g" and "plot" in n.classes()]
        for plot in plots:
            plots_seen += 1
            box = _plot_box(axes)
            series = plot_marks(plot)
            for s in series:
                n_marks += len(s.points) + len(s.boxes) + sum(1 for ln in s.lines if ln)
                if box:
                    x0, y0, x1, y1 = box
                    pts = list(s.points) + [p for ln in s.lines for p in ln] + \
                        [(b[0], b[1]) for b in s.boxes] + [(b[2], b[3]) for b in s.boxes]
                    inside_bad += sum(1 for x, y in pts if x < x0 - 1.5 or x > x1 + 1.5 or y < y0 - 1.5 or y > y1 + 1.5)
                    width = x1 - x0
                    npts = sum(len(ln) for ln in s.lines)
                    if npts > width > 0:
                        style.append(Finding("points_exceed_pixels", False,
                                             f"a series draws {npts} points across {width:.0f} px", npts / width))
            lanes = axes.get("y") is not None and axes["y"].kind == "band"    # rows apart by position
            if not lanes and len(series) > 1:
                sigs: dict[tuple, str] = {}
                for s in series:
                    sig = _signature(s.node)
                    if sig in sigs:
                        alike.append(f"{s.name[:30]} / {sigs[sig][:30]}")
                    sigs[sig] = s.name
        for which, axis in axes.items():
            if which == "z" or axis.kind == "band" or len(axis.ticks) < 2:
                continue
            pos = [t.x if which == "x" else t.y for t in axis.ticks]
            length = max(pos) - min(pos)
            if length > 0 and len(axis.ticks) > length / 22 + 1:
                style.append(Finding("too_many_ticks", False, f"the {which} axis has {len(axis.ticks)} ticks over "
                                     f"{length:.0f} px", len(axis.ticks)))
    if plots_seen:
        technical.append(Finding("has_marks", n_marks > 0, f"{n_marks} marks drawn" if n_marks else
                                 "the plot holds no marks (an empty chart)", float(n_marks)))
        technical.append(Finding("marks_inside_plot", inside_bad == 0, "every mark lies inside the plot area"
                                 if not inside_bad else f"{inside_bad} mark point(s) lie outside the plot area",
                                 float(inside_bad)))
        technical.append(Finding("series_distinguishable", not alike, "every series has its own style" if not alike
                                 else f"series look alike: {'; '.join(alike[:4])}", float(len(alike))))

    # Colour: palette and contrast.
    t2, s2 = check_colours(root, texts)
    technical += t2
    style += s2
    return technical, style


def _plot_box(axes):
    def extent(axis, which):
        if axis.domain:
            return axis.domain
        pos = sorted(t.x if which == "x" else t.y for t in axis.ticks)
        if not pos:
            return None
        half = (pos[1] - pos[0]) / 2 if len(pos) > 1 else 20.0
        return pos[0] - half, pos[-1] + half
    if "x" not in axes or "y" not in axes:
        return None
    ex, ey = extent(axes["x"], "x"), extent(axes["y"], "y")
    if not ex or not ey:
        return None
    return ex[0], ey[0], ex[1], ey[1]


def _signature(group: Elem) -> tuple:
    sig = []
    for n in group.iter():
        if n is group:
            continue
        c = n.classes()
        if n.tag == "polyline" and "series" in c:
            sig.append(("line", n.attrs.get("stroke-dasharray", "").strip()))
        elif n.tag == "circle" and "mark" in c:
            sig.append(("circle", n.attrs.get("r"), "paper" in n.attrs.get("fill", ""), "stroke" in n.attrs))
        elif n.tag == "rect" and "mark" in c:
            sig.append(("rect", n.attrs.get("fill"), n.attrs.get("fill-opacity")))
        elif {"glyph", "marker"} & c:
            d = n.attrs.get("d", "")
            sig.append((n.tag, d.count("L"), d.count("M"), n.attrs.get("fill", "")[:12]))
        if len(sig) >= 4:
            break
    flags = tuple(sorted({"warning", "danger"} & group.classes()))
    return tuple(sorted(set(sig))) + flags


def check_colours(root: Elem, texts) -> tuple[list[Finding], list[Finding]]:
    col = Colours(root)
    technical: list[Finding] = []
    style: list[Finding] = []
    hue_bad: list[str] = []
    green: list[str] = []
    # CSS custom properties and declarations.
    css = "".join(n.text for n in root.iter() if n.tag == "style")
    for media, sel, decls in css_rules(css):
        for k, v in decls.items():
            for rgb, raw in _literals(v):
                if is_green(rgb):
                    green.append(raw)
                if not is_gray(rgb) and not (k.startswith("--") and ("warn" in k or "danger" in k)):
                    hue_bad.append(f"CSS {k}: {raw}")
    flagged_ids = set()
    for n in root.iter():
        if {"warning", "danger"} & n.classes():
            for d in n.iter():
                flagged_ids.add(id(d))
    for n in root.iter():
        for prop in PAINT_ATTRS:
            if prop not in n.attrs:
                continue
            raw = n.attrs[prop]
            for mode in ("light", "dark"):
                value = resolve(raw, col.styles[mode])
                rgb = parse_color(value) if value and value.strip().lower() != "currentcolor" else None
                if rgb is None:
                    continue
                if is_green(rgb):
                    green.append(f"{n.tag} {prop}")
                if not is_gray(rgb) and id(n) not in flagged_ids:
                    hue_bad.append(f"{n.tag} {prop}={raw[:24]}")
    technical.append(Finding("palette_gray", not hue_bad, "only grays, and hue only on flagged warning/danger marks"
                             if not hue_bad else f"{len(hue_bad)} hue(s) outside a flagged mark, e.g. {hue_bad[0]}",
                             float(len(hue_bad))))
    technical.append(Finding("no_green", not green, "no green anywhere" if not green else
                             f"green is used ({len(green)} place(s))", float(len(green))))

    # Contrast.
    cells = []
    for n in root.iter():
        if n.tag == "rect" and "mark" in n.classes():
            g = mark_geometry(n)
            cells.append((g[1:], n))
    worst_text = {"light": 99.0, "dark": 99.0}
    for t in texts:
        if any(a.tag in ("title", "desc") for a in t.ancestors()):
            continue
        poly, _lab, _s = text_box(t)
        cxm = sum(p[0] for p in poly) / 4
        cym = sum(p[1] for p in poly) / 4
        for mode in ("light", "dark"):
            paint = col.paint(t, "fill", mode)
            if paint is None:
                continue
            bg = col.paper[mode]
            for (x0, y0, x1, y1), cell in cells:
                if x0 <= cxm <= x1 and y0 <= cym <= y1:
                    cp = col.paint(cell, "fill", mode)
                    if cp:
                        bg = col.over(cp, mode)
            fg = col.over(paint, mode, bg)
            worst_text[mode] = min(worst_text[mode], ratio(fg, bg))
    worst_mark = {"light": 99.0, "dark": 99.0}
    heat_scopes = {id(n) for n in root.iter() if n.tag == "g" and "plot" in n.classes()
                   and "z" in axes_of(n.parent or root)}
    for n in root.iter():
        c = n.classes()
        is_line = n.tag == "polyline" and "series" in c
        is_mark = n.tag in ("circle", "rect") and "mark" in c
        if not (is_line or is_mark):
            continue
        if n.tag == "rect" and any(id(a) in heat_scopes for a in n.ancestors()):
            continue
        for mode in ("light", "dark"):
            best = 0.0
            for prop in (("stroke",) if is_line else ("fill", "stroke")):
                if prop == "stroke" and fnum(n.attrs.get("stroke-width"), 1.0) < 0.75 and "stroke-width" in n.attrs:
                    continue
                paint = col.paint(n, prop, mode)
                if paint is None:
                    continue
                best = max(best, ratio(col.over(paint, mode), col.paper[mode]))
            if best:
                worst_mark[mode] = min(worst_mark[mode], best)
    for kind, worst, need in (("text", worst_text, TEXT_CONTRAST), ("marks", worst_mark, MARK_CONTRAST)):
        low = min(worst.values())
        if low >= 99:
            continue
        technical.append(Finding(f"contrast_{kind}", low >= need,
                                 f"{kind} contrast is at least {low:.1f}:1 in light ({worst['light']:.1f}) and dark "
                                 f"({worst['dark']:.1f}) (needs {need}:1)", round(low, 2)))
    return technical, style


def _literals(value: str):
    for m in _HEX.finditer(value or ""):
        rgb = parse_color(m.group(0))
        if rgb:
            yield rgb, m.group(0)
    for m in _RGB.finditer(value or ""):
        rgb = parse_color(m.group(0) + ")")
        if rgb:
            yield rgb, m.group(0)
    for word in re.findall(r"[a-zA-Z]+", value or ""):
        if word.lower() in NAMED and word.lower() not in ("black", "white", "gray", "grey", "silver"):
            yield NAMED[word.lower()], word


__all__ = ["Colours", "check_colours", "check_layout", "css_rules", "parse_color", "ratio", "root_styles",
           "text_box"]
