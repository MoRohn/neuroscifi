"""What every SVG this lane makes shares: escaping, number formatting, the text width model, the palette and styles.

Strings from a specification are TEXT: every one passes `esc` on its way into the output and is never used as
markup, as script, as an address or as a path (DESIGN §6).

Text width model (no font metrics are available here, and nothing made may need a font file): a string's width is
the sum of per-character estimates in em (`CHAR_EM`), times the font size. The estimates are deliberately wide for
common sans-serif faces (system-ui, Helvetica, DejaVu Sans), so a layout that fits by this model fits on screen;
the cost is some white space. Characters outside ASCII count as 1 em (wide scripts).

Palette (the owner's rule): grayscale on `currentColor` with CSS variables for light and dark; hue only for the two
flagged states, warning and danger. Series are told apart by dash pattern, marker shape and fill pattern, never by
hue (`SERIES_STYLES`, six styles: more series than that is a finding and a repair to small multiples).
"""
from __future__ import annotations

import hashlib
import json

XMLNS = "http://www.w3.org/2000/svg"
FONT = "system-ui,-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif"
FONT_MONO = "ui-monospace,SFMono-Regular,Menlo,Consolas,monospace"

# Light / dark values. Ink and paper are grays; warn (amber) and danger (red) are the only hues.
LIGHT = {"ink": "#1c1c1c", "paper": "#ffffff", "warn": "#9a5b00", "danger": "#b3261e"}
DARK = {"ink": "#e6e6e6", "paper": "#161616", "warn": "#f0a23c", "danger": "#ff7a6e"}
MUTED = 0.68        # opacity of secondary text (tick labels, axis labels): >= 4.5:1 on both papers
GRID = 0.14         # opacity of grid lines (not text, not marks)
AXIS = 0.55         # opacity of axis lines and tick marks

_NARROW = set("ijlI.,:;'|!()[]{}` ")
_WIDE = set("mwMW@%&")
CHAR_EM = {"narrow": 0.34, "digit": 0.6, "lower": 0.58, "upper": 0.72, "wide": 0.95, "other": 1.0}

# (dasharray, marker, bar fill) per series index. Markers: circle, square, triangle, diamond, cross, ring.
SERIES_STYLES = (
    ("", "circle", "solid"),
    ("9 4", "square", "hatch"),
    ("0.5 4.5", "triangle", "light"),
    ("12 4 3 4", "diamond", "cross"),
    ("6 3 2 3 2 3", "cross", "dots"),
    ("4 3", "ring", "hollow"),
)
EDGE_DASHES = ("", "8 4", "0.5 4.5", "12 4 3 4", "6 3 2 3 2 3", "4 3")


class Fit(ValueError):
    """A layout the generator cannot make readable within its bounds (the plain reason says which)."""


def esc(text) -> str:
    """XML/HTML text and attribute escaping of every string that came from a specification."""
    return (str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;").replace("'", "&#39;"))


def num(value: float, places: int = 2) -> str:
    """A coordinate: fixed places, trailing zeros removed, never '-0', never an exponent (same bytes on any host)."""
    text = f"{float(value):.{places}f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text in ("-0", "") else text


def fixed(value: float, decimals: int) -> str:
    """A tick or table value as a plain decimal with exactly `decimals` places; never '-0' or an exponent."""
    text = f"{float(value):.{max(0, decimals)}f}"
    if text.lstrip("-").strip("0.") == "":
        text = text.lstrip("-")
    return text


def char_em(ch: str) -> float:
    if ch in _NARROW:
        return CHAR_EM["narrow"]
    if ch in _WIDE:
        return CHAR_EM["wide"]
    if ch.isdigit():
        return CHAR_EM["digit"]
    if "a" <= ch <= "z":
        return CHAR_EM["lower"]
    if "A" <= ch <= "Z":
        return CHAR_EM["upper"]
    if ord(ch) < 128:
        return CHAR_EM["lower"]
    return CHAR_EM["other"]


def text_width(text: str, size: float) -> float:
    return sum(char_em(c) for c in str(text)) * size


def abbreviate(text: str, max_width: float, size: float) -> tuple[str, bool]:
    """`text` cut with an ellipsis to fit `max_width` by the width model; (shown, was_cut)."""
    text = str(text)
    if text_width(text, size) <= max_width:
        return text, False
    budget = max_width - text_width("…", size)
    out = ""
    for ch in text:
        if text_width(out + ch, size) > budget:
            break
        out += ch
    return (out.rstrip() or text[:1]) + "…", True


def attrs(pairs) -> str:
    """Attributes in the given order; None values are left out; every value escaped."""
    return "".join(f' {k}="{esc(v)}"' for k, v in pairs if v is not None)


def el(tag: str, pairs=(), body: str | None = None) -> str:
    if body is None:
        return f"<{tag}{attrs(pairs)}/>"
    return f"<{tag}{attrs(pairs)}>{body}</{tag}>"


def text_el(x: float, y: float, text: str, *, size: float = 12, anchor: str = "start", cls: str | None = None,
            weight: str | None = None, opacity: float | None = None, rotate: float | None = None,
            full: str | None = None, fill: str = "currentColor", baseline: str | None = None) -> str:
    """A <text> element. `full` (the uncut label when `text` was abbreviated) goes into a <title> child."""
    pairs = [("class", cls), ("x", num(x)), ("y", num(y)), ("font-size", num(size)), ("text-anchor", anchor),
             ("font-weight", weight), ("fill", fill),
             ("fill-opacity", num(opacity) if opacity is not None else None),
             ("dominant-baseline", baseline),
             ("transform", f"rotate({num(rotate)} {num(x)} {num(y)})" if rotate else None)]
    body = esc(text) + (el("title", (), esc(full)) if full is not None else "")
    return el("text", pairs, body)


def scope_id(form: str, spec_json: str) -> str:
    """A deterministic id for one picture (scopes its CSS and pattern ids when several share an HTML page)."""
    return "nsf-" + hashlib.sha256((form + "\n" + spec_json).encode("utf-8")).hexdigest()[:10]


def style_block(sid: str, extra: str = "") -> str:
    """The picture's CSS: palette variables for light and dark, scoped to this picture's id."""
    def vars_(p):
        return (f"--nsf-ink:{p['ink']};--nsf-paper:{p['paper']};--nsf-warn:{p['warn']};"
                f"--nsf-danger:{p['danger']};")
    css = (f"#{sid}{{{vars_(LIGHT)}color:var(--nsf-ink);background:var(--nsf-paper);font-family:{FONT}}}"
           f"@media (prefers-color-scheme:dark){{#{sid}{{{vars_(DARK)}}}}}" + extra)
    return el("style", (), css)


def flag_color(flag: str | None) -> str:
    return {"warning": "var(--nsf-warn)", "danger": "var(--nsf-danger)"}.get(flag or "", "currentColor")


def metadata(table: dict) -> str:
    """The source table, once, as JSON inside <metadata> (for editing and accessibility; no reader judges by it)."""
    raw = json.dumps(table, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)
    raw = raw.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    return el("metadata", (), raw)


def svg_open(sid: str, form: str, width: float, height: float) -> str:
    return (f'<svg xmlns="{XMLNS}" id="{sid}" class="nsf-picture" data-form="{esc(form)}" width="{num(width)}" '
            f'height="{num(height)}" viewBox="0 0 {num(width)} {num(height)}" role="img" '
            f'aria-labelledby="{sid}-t {sid}-d">')


def head(sid: str, form: str, width: float, height: float, title: str, desc: str, table: dict | None,
         css_extra: str = "", defs: str = "") -> str:
    """Root element, <title>, <desc>, <metadata>, <style>, <defs> and the paper."""
    out = [svg_open(sid, form, width, height), el("title", (("id", f"{sid}-t"),), esc(title)),
           el("desc", (("id", f"{sid}-d"),), esc(desc))]
    if table is not None:
        out.append(metadata(table))
    out.append(style_block(sid, css_extra))
    if defs:
        out.append(el("defs", (), defs))
    out.append(el("rect", (("class", "paper"), ("width", num(width)), ("height", num(height)),
                           ("fill", "var(--nsf-paper)"))))
    return "".join(out)


def patterns(sid: str) -> str:
    """Fill patterns for bars and areas (hatch, cross, dots), drawn in currentColor."""
    def pat(name, body):
        return el("pattern", (("id", f"{sid}-{name}"), ("patternUnits", "userSpaceOnUse"), ("width", "6"),
                              ("height", "6")), body)
    line = (("stroke", "currentColor"), ("stroke-width", "1.3"), ("fill", "none"))
    return (pat("hatch", el("rect", (("width", "6"), ("height", "6"), ("fill", "var(--nsf-paper)")))
                + el("path", (("d", "M-1,1 L1,-1 M0,6 L6,0 M5,7 L7,5"),) + line))
            + pat("cross", el("rect", (("width", "6"), ("height", "6"), ("fill", "var(--nsf-paper)")))
                  + el("path", (("d", "M0,3 L6,3 M3,0 L3,6"),) + line))
            + pat("dots", el("rect", (("width", "6"), ("height", "6"), ("fill", "var(--nsf-paper)")))
                  + el("circle", (("cx", "3"), ("cy", "3"), ("r", "1.3"), ("fill", "currentColor")))))


def bar_fill(sid: str, kind: str) -> tuple[str, str | None]:
    """(fill, fill-opacity) of a bar fill style."""
    if kind == "solid":
        return "currentColor", "0.82"
    if kind == "light":
        return "currentColor", "0.3"
    if kind == "hollow":
        return "var(--nsf-paper)", None
    return f"url(#{sid}-{kind})", None


def marker(kind: str, x: float, y: float, color: str, size: float = 4.0, cls: str = "marker") -> str:
    """A marker glyph centred on (x, y). Used for line-chart markers and legend keys (decoration, not a mark)."""
    s = size
    stroke = (("stroke", color), ("stroke-width", "1.5"))
    if kind == "circle":
        return el("circle", (("class", cls), ("cx", num(x)), ("cy", num(y)), ("r", num(s * 0.85)), ("fill", color)))
    if kind == "ring":
        return el("circle", (("class", cls), ("cx", num(x)), ("cy", num(y)), ("r", num(s * 0.85)),
                             ("fill", "var(--nsf-paper)")) + stroke)
    if kind == "square":
        return el("rect", (("class", cls), ("x", num(x - s * 0.8)), ("y", num(y - s * 0.8)), ("width", num(s * 1.6)),
                           ("height", num(s * 1.6)), ("fill", "var(--nsf-paper)")) + stroke)
    if kind == "triangle":
        d = f"M{num(x)},{num(y - s)} L{num(x + s)},{num(y + s * 0.8)} L{num(x - s)},{num(y + s * 0.8)} Z"
        return el("path", (("class", cls), ("d", d), ("fill", color)))
    if kind == "diamond":
        d = f"M{num(x)},{num(y - s * 1.1)} L{num(x + s)},{num(y)} L{num(x)},{num(y + s * 1.1)} L{num(x - s)},{num(y)} Z"
        return el("path", (("class", cls), ("d", d), ("fill", "var(--nsf-paper)")) + stroke)
    d = f"M{num(x - s)},{num(y - s)} L{num(x + s)},{num(y + s)} M{num(x - s)},{num(y + s)} L{num(x + s)},{num(y - s)}"
    return el("path", (("class", cls), ("d", d), ("fill", "none"), ("stroke", color), ("stroke-width", "1.8")))


def contrast(a: tuple[int, int, int], b: tuple[int, int, int]) -> float:
    """WCAG 2 contrast ratio of two sRGB colours."""
    def lum(c):
        def ch(v):
            v = v / 255
            return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4
        r, g, b_ = (ch(v) for v in c)
        return 0.2126 * r + 0.7152 * g + 0.0722 * b_
    hi, lo = sorted((lum(a), lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def blend(fg, bg, alpha: float) -> tuple[int, int, int]:
    return tuple(round(f * alpha + b * (1 - alpha)) for f, b in zip(fg, bg))


def gray_hex(level: int) -> str:
    level = max(0, min(255, int(level)))
    return f"#{level:02x}{level:02x}{level:02x}"


__all__ = ["AXIS", "CHAR_EM", "DARK", "EDGE_DASHES", "FONT", "Fit", "GRID", "LIGHT", "MUTED", "SERIES_STYLES",
           "XMLNS", "abbreviate", "attrs", "bar_fill", "blend", "contrast", "el", "esc", "fixed", "flag_color",
           "gray_hex", "head", "marker", "metadata", "num", "patterns", "scope_id", "style_block", "text_el",
           "text_width"]
