"""What a person would see of a made picture (fix lane F4, item 2: measurement review, second pass, R2-1 and R2-2).

The value checks read marks and ticks from the picture's geometry; this check asks, independently, whether a person
would see them at all. Written from the SVG and CSS rules themselves, not from the benchmark's reader. A part of the
picture that matters (the plot, a mark, a series, an axis, a tick's text) is UNSEEN when, in the light or the dark
scheme:
- `display` is none on it or an ancestor, its `visibility` is hidden or collapse, or its opacity (the product of its
  ancestors') is zero, or both its fill and its stroke opacity are zero;
- it is clipped by a clip path of zero area;
- a tick's text has a zero font size, a mark circle has no radius;
- every paint it has (fill and stroke) is none or the background's colour (the paper rect painted under the plot, else
  the picture's own background, else white);
- a mark lies outside the canvas, or an opaque shape painted after it covers it (inside the plot only shapes larger
  than one percent of the canvas count, so a line chart's point markers over its line do not);
and the whole picture is unseen when its root has zero width or height. Presentation attributes, `style` attributes
and `<style>` rules all count (later rules win; `@media` rules for the dark scheme count only there, other `@media`
rules in both). An animated picture's frame counts as seen when some frame of its `@keyframes` shows it, and as unseen
when every frame hides it. A style rule this check cannot match (a pseudo-class, an attribute selector, a sibling
combinator) that hides is a finding too: what cannot be read is not shown to be visible.
"""
from __future__ import annotations

import re

from . import Finding
from .svg_read import Elem, apply, canvas, ctm, fnum, mark_geometry, points_of

SCHEMES = ("light", "dark")
_COMPOUND = re.compile(r"^(\*|[A-Za-z][\w-]*)?((?:[.#][\w-]+)*)$")
_PART = re.compile(r"[.#][\w-]+")
_VAR = re.compile(r"var\(\s*(--[\w-]+)\s*(?:,\s*([^)]*))?\)")
_NAMED = {"white": "#ffffff", "black": "#000000", "transparent": "transparent", "none": "none"}
_SHAPES = ("rect", "circle", "ellipse", "polygon", "polyline", "path", "line")
_HIDING = {"display": ("none",), "visibility": ("hidden", "collapse")}
_INHERITED = ("fill", "stroke", "color", "visibility", "font-size", "fill-opacity", "stroke-opacity", "stroke-width")
_DEFAULT = {"fill": "#000000", "stroke": "none", "color": "#000000", "visibility": "visible", "font-size": "16",
            "stroke-width": "1",
            "fill-opacity": "1", "stroke-opacity": "1", "opacity": "1", "display": "inline"}


def _declarations(body: str) -> dict:
    out = {}
    for part in body.split(";"):
        if ":" in part:
            name, value = part.split(":", 1)
            out[name.strip().lower()] = value.replace("!important", "").strip()
    return out


def _blocks(css: str):
    """(prelude, body) of every top-level block of `css`, nested braces kept in the body."""
    depth, start, prelude_at = 0, 0, 0
    for i, ch in enumerate(css):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}" and depth:
            depth -= 1
            if depth == 0:
                yield css[prelude_at:start].strip(), css[start + 1:i]
                prelude_at = i + 1


def _parse_selector(text: str):
    """[(combinator, tag, classes, ids)] right to left, or None for a selector this check does not read."""
    tokens = text.replace(">", " > ").split()
    out, comb = [], " "
    for token in tokens:
        if token == ">":
            comb = ">"
            continue
        m = _COMPOUND.match(token)
        if not m or not token:
            return None
        parts = _PART.findall(m.group(2) or "")
        out.append((comb, m.group(1), {p[1:] for p in parts if p[0] == "."}, {p[1:] for p in parts if p[0] == "#"}))
        comb = " "
    return out[::-1] if out else None


def _compound_ok(node: Elem, tag, classes, ids) -> bool:
    return ((tag in (None, "*") or node.tag == tag) and classes <= node.classes()
            and (not ids or node.attrs.get("id") in ids and len(ids) == 1))


def _matches(node: Elem, sel) -> bool:
    (_, tag, classes, ids), rest = sel[0], sel[1:]
    if not _compound_ok(node, tag, classes, ids):
        return False
    if not rest:
        return True
    comb = sel[0][0]
    if comb == ">":
        return node.parent is not None and _matches(node.parent, rest)
    return any(_matches(a, rest) for a in node.ancestors())


class _Style:
    """The picture's CSS, read once: rules per scheme, and what could not be read."""

    def __init__(self, root: Elem):
        self.rules = {s: [] for s in SCHEMES}      # [(selector, declarations)] in document order
        self.keyframes: dict[str, list[dict]] = {}  # name -> the declarations of each frame
        self.problems: list[str] = []
        for node in root.iter():
            if node.tag == "style":
                self._read(node.text or "", SCHEMES)

    def _read(self, css: str, schemes):
        for prelude, body in _blocks(re.sub(r"/\*.*?\*/", "", css, flags=re.S)):
            low = prelude.lower()
            if low.startswith("@media"):
                inner = tuple(s for s in SCHEMES if not ("prefers-color-scheme" in low and s not in low))
                self._read(body, tuple(s for s in schemes if s in inner))
            elif low.startswith("@keyframes"):
                name = prelude.split()[1] if len(prelude.split()) > 1 else ""
                self.keyframes[name] = [_declarations(frame) for _, frame in _blocks(body)]
            elif not low.startswith("@"):
                decls = _declarations(body)
                for text in prelude.split(","):
                    sel = _parse_selector(text)
                    if sel is None:
                        if _hides(decls):
                            self.problems.append(f"a style rule this check cannot read hides something "
                                                 f"({text.strip()[:40]})")
                        continue
                    for scheme in schemes:
                        self.rules[scheme].append((sel, decls))

    def _frames(self, node: Elem, scheme: str) -> list[dict]:
        names = (self.own(node, "animation-name", scheme) or "").split(",") + (
            self.own(node, "animation", scheme) or "").replace(",", " ").split()
        return [f for n in names if n.strip() in self.keyframes for f in self.keyframes[n.strip()]]

    def animated(self, node: Elem, scheme: str):
        """None when no known animation applies to `node` itself; True when one shows it in some frame (an animated
        picture's frames: their static opacity does not hide them); False when every frame of it hides."""
        frames = self._frames(node, scheme)
        return None if not frames else any(not _hides(f) for f in frames)

    def sometimes_hidden(self, node: Elem, scheme: str) -> bool:
        """An animation hides `node` in some frame (one frame of an animated picture, which covers no other)."""
        return any(_hides(f) for f in self._frames(node, scheme))

    def own(self, node: Elem, prop: str, scheme: str):
        value = node.attrs.get(prop)
        for sel, decls in self.rules[scheme]:
            if prop in decls and _matches(node, sel):
                value = decls[prop]
        inline = _declarations(node.attrs.get("style", "")).get(prop)
        return inline if inline is not None else value

    def get(self, node: Elem, prop: str, scheme: str):
        while node is not None:
            value = self.own(node, prop, scheme)
            if value is not None and value.lower() != "inherit":
                return value
            if prop not in _INHERITED:
                return _DEFAULT.get(prop)
            node = node.parent
        return _DEFAULT.get(prop)

    def custom(self, root: Elem, name: str, scheme: str):
        value = None
        for sel, decls in self.rules[scheme]:
            if name in decls and _matches(root, sel):
                value = decls[name]
        return value


def _hides(decls: dict) -> bool:
    if any(decls.get(p, "").lower() in v for p, v in _HIDING.items()):
        return True
    return any(_zero(decls.get(p)) for p in ("opacity", "font-size")) or (
        _zero(decls.get("fill-opacity")) and _zero(decls.get("stroke-opacity")))


def _zero(value) -> bool:
    m = re.match(r"\s*(-?\d*\.?\d+)", str(value)) if value is not None else None
    return bool(m) and float(m.group(1)) <= 0.001


def _colour(style: _Style, root: Elem, node: Elem, value, scheme: str, depth=0) -> str:
    """A paint as '#rrggbb', 'none' or 'other' (a pattern, a gradient, a name this check does not know)."""
    value = (value or "").strip()
    low = value.lower()
    if depth > 8:
        return "other"
    m = _VAR.fullmatch(value)
    if m:
        found = style.custom(root, m.group(1), scheme)
        return _colour(style, root, node, found if found is not None else m.group(2), scheme, depth + 1)
    if low == "currentcolor":
        return _colour(style, root, node, style.get(node, "color", scheme), scheme, depth + 1)
    if low in _NAMED:
        return _NAMED[low] if low != "transparent" else "none"
    if re.fullmatch(r"#[0-9a-f]{3}", low):
        return "#" + "".join(c * 2 for c in low[1:])
    if re.fullmatch(r"#[0-9a-f]{6}", low):
        return low
    return "other"


def _box(node: Elem, view) -> tuple[float, float, float, float] | None:
    """The root-space bounding box of a shape (percentages of the canvas read as such)."""
    x0, y0, x1, y1 = view

    def length(name, full):
        text = str(node.attrs.get(name, "0")).strip()
        return full * fnum(text[:-1]) / 100 if text.endswith("%") else fnum(text)
    if node.tag == "rect":
        x, y = length("x", x1 - x0), length("y", y1 - y0)
        pts = [(x, y), (x + length("width", x1 - x0), y + length("height", y1 - y0))]
    elif node.tag in ("circle", "ellipse"):
        cx, cy = fnum(node.attrs.get("cx")), fnum(node.attrs.get("cy"))
        rx = fnum(node.attrs.get("r", node.attrs.get("rx")))
        ry = fnum(node.attrs.get("r", node.attrs.get("ry")))
        pts = [(cx - rx * 0.7, cy - ry * 0.7), (cx + rx * 0.7, cy + ry * 0.7)]     # the square inside it
    elif node.tag in ("polygon", "path"):
        pts = points_of(node.attrs.get("points") if node.tag == "polygon" else
                        re.sub(r"[A-Za-z]", " ", node.attrs.get("d", "")))
        if len(pts) < 3:
            return None
    else:
        return None
    m = ctm(node)
    corners = [apply(m, a, b) for a, b in pts] + [apply(m, a, b) for a, b in
                                                  ((pts[0][0], pts[-1][1]), (pts[-1][0], pts[0][1]))]
    xs, ys = [c[0] for c in corners], [c[1] for c in corners]
    return min(xs), min(ys), max(xs), max(ys)


def _centre(node: Elem):
    g = mark_geometry(node)
    if g is None:
        return []
    if g[0] == "point":
        return [g[1:]]
    if g[0] == "box":
        return [((g[1] + g[3]) / 2, (g[2] + g[4]) / 2)]
    return list(g[1])


def _clip_area_zero(root_ids: dict, ref: str) -> bool:
    m = re.match(r"\s*url\(\s*['\"]?#([^)'\"]+)", ref or "")
    clip = root_ids.get(m.group(1)) if m else None
    if clip is None or clip.tag != "clipPath":
        return False
    for shape in clip.iter():
        if shape is clip:
            continue
        a = shape.attrs
        if shape.tag == "rect" and fnum(a.get("width")) > 0 and fnum(a.get("height")) > 0:
            return False
        if shape.tag == "circle" and fnum(a.get("r")) > 0:
            return False
        if shape.tag == "ellipse" and fnum(a.get("rx")) > 0 and fnum(a.get("ry")) > 0:
            return False
        if shape.tag in ("polygon", "path", "text"):
            return False        # an area this check does not measure: not called zero
    return True


def unseen(root: Elem) -> list[str]:
    """Plain reasons why a person would not see the parts that matter; [] when nothing hides them."""
    style = _Style(root)
    problems = list(dict.fromkeys(style.problems))
    view = canvas(root)
    for attr in ("width", "height"):
        if attr in root.attrs and fnum(root.attrs[attr], 1.0) <= 0:
            problems.append("the picture's own size is zero")
    if view[2] - view[0] <= 0 or view[3] - view[1] <= 0:
        problems.append("the picture's view box has no area")
    order = list(root.iter())
    position = {id(n): i for i, n in enumerate(order)}
    ids = {n.attrs["id"]: n for n in order if "id" in n.attrs}
    plots = [n for n in order if n.tag == "g" and "plot" in n.classes()]
    marks = [n for n in order if n.tag in _SHAPES and n.classes() & {"mark", "series"}]
    ticks = [n for n in order if n.tag == "text" and any("tick" in a.classes() for a in n.ancestors())]
    if not plots and not marks:
        return problems
    in_defs = {id(n) for d in order if d.tag in ("defs", "clipPath", "pattern", "linearGradient")
               for n in d.iter()}
    paper = next((n for n in order if n.tag == "rect" and id(n) not in in_defs
                  and (not plots or position[id(n)] < position[id(plots[0])])
                  and (b := _box(n, view)) and (b[2] - b[0]) * (b[3] - b[1]) >= 0.9 * (view[2] - view[0])
                  * (view[3] - view[1])), None)
    area = (view[2] - view[0]) * (view[3] - view[1])
    found: dict[str, int] = {}

    def note(words):
        found[words] = found.get(words, 0) + 1

    for scheme in SCHEMES:
        background = _colour(style, root, paper, style.get(paper, "fill", scheme), scheme) if paper is not None \
            else _colour(style, root, root, style.own(root, "background", scheme) or "#ffffff", scheme)
        for node in plots + marks + ticks + [a for a in order if a.tag == "g" and "axis" in a.classes()]:
            what = ("the plot" if node in plots else "a tick's text" if node.tag == "text" else
                    "an axis" if node.tag == "g" else "a mark")
            chain = [node, *node.ancestors()]
            if any((style.own(n, "display", scheme) or "").lower() == "none" for n in chain):
                note(f"{what} is not displayed (display: none)")
                continue
            if style.get(node, "visibility", scheme).lower() in ("hidden", "collapse"):
                note(f"{what} is hidden (visibility)")
                continue
            if any(style.animated(n, scheme) is False for n in chain):
                note(f"{what} is hidden in every frame of its animation")
                continue
            if any(_zero(style.own(n, "opacity", scheme)) and not style.animated(n, scheme) for n in chain):
                note(f"{what} is fully transparent (opacity 0)")
                continue
            if any(_clip_area_zero(ids, style.own(n, "clip-path", scheme)) for n in chain):
                note(f"{what} is clipped to nothing")
                continue
            if node.tag == "text" and _zero(style.get(node, "font-size", scheme)):
                note(f"{what} has a zero font size")
                continue
            if node.tag == "g":
                continue
            if node.tag == "circle" and fnum(node.attrs.get("r")) <= 0:
                note(f"{what} is a circle with no radius")
                continue
            paints = []
            for prop in (("stroke",) if node.tag == "line" else ("fill", "stroke")):
                if _zero(style.get(node, f"{prop}-opacity", scheme)) or (
                        prop == "stroke" and _zero(style.get(node, "stroke-width", scheme))):
                    continue
                paints.append(_colour(style, root, node, style.get(node, prop, scheme), scheme))
            if all(p in ("none", background) for p in paints):
                note(f"{what} is painted only in the background's colour (or not at all)")
                continue
            if node.tag == "text" or scheme == "dark":
                continue
            centres = _centre(node)
            if centres and all(not (view[0] - 1 <= x <= view[2] + 1 and view[1] - 1 <= y <= view[3] + 1)
                               for x, y in centres):
                note(f"{what} lies outside the picture")
                continue
            if centres and _covered(style, root, node, centres, order, position, in_defs, view, area, plots, scheme):
                note(f"{what} is covered by an opaque shape painted after it")
    return problems + [f"{words} ({n} time{'s' if n > 1 else ''})" if n > 1 else words for words, n in found.items()]


def _covered(style, root, node, centres, order, position, in_defs, view, area, plots, scheme) -> bool:
    inside = {id(n) for p in plots for n in p.iter()}
    mine = {id(node), *(id(a) for a in node.ancestors())}
    for other in order[position[id(node)] + 1:]:
        if other.tag not in _SHAPES or other.tag in ("line", "polyline") or id(other) in in_defs or id(other) in mine:
            continue
        if any((style.own(n, "display", scheme) or "").lower() == "none" for n in [other, *other.ancestors()]):
            continue
        fill = _colour(style, root, other, style.get(other, "fill", scheme), scheme)
        if fill == "none" or style.get(other, "visibility", scheme).lower() in ("hidden", "collapse") or any(
                style.sometimes_hidden(n, scheme) for n in [other, *other.ancestors()]):
            continue
        opacity = 1.0
        for n in [other, *other.ancestors()]:
            opacity *= fnum(style.own(n, "opacity", scheme), 1.0)
        if opacity * fnum(style.get(other, "fill-opacity", scheme), 1.0) < 0.99:
            continue
        box = _box(other, view)
        if box is None:
            continue
        if id(other) in inside and (box[2] - box[0]) * (box[3] - box[1]) < 0.01 * area:
            continue            # a point marker over its line, a bar beside another
        if all(box[0] <= x <= box[2] and box[1] <= y <= box[3] for x, y in centres):
            return True
    return False


__all__ = ["unseen", "visible_finding"]


def visible_finding(root: Elem) -> Finding:
    problems = unseen(root)
    return Finding("visible_to_a_person", not problems,
                   "nothing hides the plot, its marks, axes or tick labels" if not problems
                   else "; ".join(problems[:6]), float(len(problems)))
