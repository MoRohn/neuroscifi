"""Safety of an SVG that will be shown: an allow-list of elements and attributes, no active content, no reference.

Written from the SVG format (not from any generator). Refused: script, foreignObject, every element not listed in
`ELEMENTS` (so no `a`, `use`, `image`, `iframe`, `animate`, `set`, ...), event attributes (`on*`), any `href`,
any attribute in another namespace, `url(...)` other than a reference to an element of the same picture (`url(#id)`
naming an existing id), and in CSS: `@import`, `@font-face`, `expression(`, `javascript:`, `behavior`, `binding`,
and any at-rule other than `@media` and `@keyframes`. A DOCTYPE, entity declaration or processing instruction is
refused by the parser itself (svg_read.parse). Sizes are bounded there too.
"""
from __future__ import annotations

import re

from . import Finding
from .svg_read import SVG_NS, Parsed

ELEMENTS = frozenset({"svg", "g", "title", "desc", "metadata", "style", "defs", "pattern", "clipPath",
                      "linearGradient", "stop", "rect", "circle", "ellipse", "line", "polyline", "polygon", "path",
                      "text", "tspan"})
ATTRIBUTES = frozenset({
    "id", "class", "transform", "x", "y", "width", "height", "cx", "cy", "r", "rx", "ry", "x1", "y1", "x2", "y2",
    "points", "d", "fill", "fill-opacity", "fill-rule", "stroke", "stroke-width", "stroke-dasharray",
    "stroke-dashoffset", "stroke-linecap", "stroke-linejoin", "stroke-opacity", "stroke-miterlimit", "opacity",
    "font-size", "font-family", "font-weight", "font-style", "text-anchor", "dominant-baseline", "letter-spacing",
    "viewBox", "preserveAspectRatio", "xmlns", "role", "lang", "patternUnits", "patternTransform",
    "gradientUnits", "offset", "stop-color", "stop-opacity", "clip-path", "clipPathUnits", "dx", "dy",
    "paint-order", "vector-effect", "shape-rendering", "color", "visibility", "display", "focusable", "tabindex"})
ATTRIBUTE_PREFIXES = ("aria-", "data-")
_URL = re.compile(r"url\(\s*(['\"]?)([^)'\"]*)\1\s*\)", re.I)
_CSS_BAD = re.compile(r"@import|@font-face|@namespace|expression\s*\(|javascript\s*:|vbscript\s*:|behavior\s*:|"
                      r"-moz-binding|binding\s*:|\\", re.I)
_AT_RULE = re.compile(r"@([a-zA-Z-]+)")


def url_refs(text: str) -> list[str]:
    return [m.group(2).strip() for m in _URL.finditer(text or "")]


def check_svg(parsed: Parsed) -> list[Finding]:
    root = parsed.root
    bad_elements: set[str] = set()
    bad_attrs: set[str] = set()
    events: set[str] = set()
    hrefs = 0
    external: list[str] = []
    ids = {n.attrs["id"] for n in root.iter() if "id" in n.attrs}
    css_problems: set[str] = set()
    for node in root.iter():
        if node.ns != SVG_NS or node.tag not in ELEMENTS:
            bad_elements.add(node.tag if node.ns == SVG_NS else "an element in another namespace")
        for name, value in node.attrs.items():
            low = name.lower()
            if low.startswith("on"):
                events.add(name)
                continue
            if "href" in low:
                hrefs += 1
                continue
            if name.startswith("{"):
                bad_attrs.add("an attribute in another namespace")
                continue
            if name not in ATTRIBUTES and not name.startswith(ATTRIBUTE_PREFIXES):
                bad_attrs.add(name)
            if name == "style":
                css_problems |= _css_problems(value)
            if name.startswith(ATTRIBUTE_PREFIXES) or name in ("id", "class", "role", "lang"):
                continue    # text: a label may say "url(...)" without being one
            for ref in url_refs(value):
                if not (ref.startswith("#") and ref[1:] in ids):
                    external.append(ref[:40])
        if node.tag == "style":
            css_problems |= _css_problems(node.text)
            for ref in url_refs(node.text):
                if not (ref.startswith("#") and ref[1:] in ids):
                    external.append(ref[:40])
    findings = [
        Finding("svg_root", root.tag == "svg" and root.ns == SVG_NS,
                "the root element is an SVG element" if root.tag == "svg" else "the root element is not <svg>"),
        Finding("svg_allowed_elements", not bad_elements,
                "every element is on the allow-list" if not bad_elements
                else f"elements not allowed: {', '.join(sorted(bad_elements)[:8])}"),
        Finding("svg_allowed_attributes", not bad_attrs,
                "every attribute is on the allow-list" if not bad_attrs
                else f"attributes not allowed: {', '.join(sorted(bad_attrs)[:8])}"),
        Finding("svg_no_event_attributes", not events,
                "no event attribute" if not events else f"{len(events)} event attribute(s), e.g. {sorted(events)[0]}"),
        Finding("svg_no_href", hrefs == 0, "no href" if hrefs == 0 else f"{hrefs} href attribute(s)"),
        Finding("svg_no_external_reference", not external,
                "every url() names an element of this picture" if not external
                else f"{len(external)} reference(s) outside the picture"),
        Finding("svg_css_safe", not css_problems,
                "the CSS holds only declarations, @media and @keyframes" if not css_problems
                else f"CSS not allowed: {', '.join(sorted(css_problems))}"),
        Finding("svg_no_foreign_namespace", not parsed.other_namespaces,
                "only the SVG namespace is used" if not parsed.other_namespaces
                else f"{len(parsed.other_namespaces)} other namespace(s)"),
    ]
    return findings


def _css_problems(css: str) -> set[str]:
    out = set()
    for m in _CSS_BAD.finditer(css or ""):
        out.add(m.group(0).strip().lower()[:20])
    for m in _AT_RULE.finditer(css or ""):
        if m.group(1).lower() not in ("media", "keyframes"):
            out.add("@" + m.group(1).lower()[:20])
    return out


__all__ = ["ATTRIBUTES", "ELEMENTS", "check_svg", "url_refs"]
