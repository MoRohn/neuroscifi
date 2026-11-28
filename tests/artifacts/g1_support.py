"""Shared by lane G1's tests: spec builders and byte-level mutations that make deliberately wrong artifacts.

The mutations edit made bytes with ElementTree (trusted test input), never through the generators."""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET

from neuroscifi.artifacts import check, make
from neuroscifi.artifacts.check import _g1 as _check_g1  # noqa: F401  (registers the validators)
from neuroscifi.artifacts.make import _g1 as _make_g1  # noqa: F401  (registers the forms and generators)

SVG = "http://www.w3.org/2000/svg"
ET.register_namespace("", SVG)


def spec(form, title="A chart", data=None, options=None, description=""):
    return {"spec": form, "version": 1, "title": title, "description": description, "data": data or {},
            "options": options or {}, "sources": ["request"], "made_by": {"generator": "", "version": 0}}


def made(s):
    return make.make(s)


def verdict(data, s, media_type="image/svg+xml", source=None):
    return check.check(data, media_type, s, source)


def failed(v):
    return {f.rule for f in v.technical if not f.passed}


def finding(v, rule):
    for f in list(v.technical) + list(v.style):
        if f.rule == rule:
            return f
    return None


def tree(data: bytes):
    return ET.fromstring(data)


def to_bytes(root) -> bytes:
    return ET.tostring(root, encoding="utf-8")


def q(tag):
    return f"{{{SVG}}}{tag}"


def classes(e):
    return set(e.get("class", "").split())


def groups(root, *names):
    return [e for e in root.iter(q("g")) if set(names) <= classes(e)]


def scale_ticks(data: bytes, axis: str, factor: float) -> bytes:
    """Multiply every numeric tick label of one axis by `factor` (a scale that is off)."""
    root = tree(data)
    for g in groups(root, "axis", axis):
        for t in g.iter(q("text")):
            if t.text and re.fullmatch(r"-?\d+(\.\d+)?", t.text.strip()):
                t.text = repr(round(float(t.text) * factor, 6))
    return to_bytes(root)


def swap_series(data: bytes) -> bytes:
    root = tree(data)
    s = [g for g in groups(root, "series") if g.get("data-name") is not None]
    a, b = s[0].get("data-name"), s[1].get("data-name")
    s[0].set("data-name", b)
    s[1].set("data-name", a)
    return to_bytes(root)


def clip_title(data: bytes) -> bytes:
    root = tree(data)
    title = next(t for t in root.iter(q("text")) if "title" in classes(t))
    title.set("x", str(float(root.get("width")) - 20))
    return to_bytes(root)


def add_script(data: bytes) -> bytes:
    root = tree(data)
    s = ET.SubElement(root, q("script"))
    s.text = "alert(1)"
    return to_bytes(root)


def green_fill(data: bytes) -> bytes:
    root = tree(data)
    for e in root.iter():
        if "series" in classes(e) and e.tag != q("g"):
            e.set("stroke", "#2e8b57")
            break
        if "mark" in classes(e):
            e.set("fill", "#2e8b57")
            break
    return to_bytes(root)


def drop_title(data: bytes) -> bytes:
    root = tree(data)
    root.remove(root.find(q("title")))
    return to_bytes(root)


def empty_plot(data: bytes) -> bytes:
    root = tree(data)
    for g in groups(root, "plot"):
        for c in list(g):
            g.remove(c)
    return to_bytes(root)


def shift_marks(data: bytes, dy: float) -> bytes:
    """Move every mark of the first series by dy pixels (values no longer match; may leave the plot)."""
    root = tree(data)
    g = [g for g in groups(root, "series") if g.get("data-name") is not None][0]
    for e in g.iter():
        if e.tag == q("polyline"):
            pts = [float(v) for v in re.findall(r"-?\d+(?:\.\d+)?", e.get("points"))]
            e.set("points", " ".join(f"{pts[i]},{pts[i + 1] + dy}" for i in range(0, len(pts), 2)))
        elif e.tag == q("circle") and "mark" in classes(e):
            e.set("cy", str(float(e.get("cy")) + dy))
        elif e.tag == q("rect") and "mark" in classes(e):
            e.set("y", str(float(e.get("y")) + dy))
    return to_bytes(root)


def faint_text(data: bytes) -> bytes:
    root = tree(data)
    for t in root.iter(q("text")):
        t.set("fill-opacity", "0.2")
    return to_bytes(root)


def same_dash(data: bytes) -> bytes:
    root = tree(data)
    for e in root.iter(q("polyline")):
        if "series" in classes(e):
            e.attrib.pop("stroke-dasharray", None)
    for e in list(root.iter()):
        for c in list(e):
            if {"marker", "glyph"} & classes(c):
                e.remove(c)
    return to_bytes(root)


def add_event(data: bytes) -> bytes:
    root = tree(data)
    root.set("onload", "alert(1)")
    return to_bytes(root)


def add_external_image(data: bytes) -> bytes:
    root = tree(data)
    img = ET.SubElement(root, q("image"))
    img.set("href", "https://example.invalid/x.png")
    return to_bytes(root)


def external_url_fill(data: bytes) -> bytes:
    root = tree(data)
    next(root.iter(q("rect"))).set("fill", "url(https://example.invalid/p.svg#x)")
    return to_bytes(root)
