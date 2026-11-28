"""An animated SVG: a time-stepped line, bar or scatter chart, by declarative CSS animation only (no script, no
SMIL).

Every frame is drawn on the same axes (the union of all frames' ranges), so the frames differ only in their marks:
the picture holds the axes, legend and grid once, and one `<g class="frame" data-label="...">` per frame, each
holding that frame's `<g class="plot">` (the charts' reading contract) and its label. Each frame is shown for
`seconds_per_frame` in turn (CSS keyframes, step timing), looping or stopping on the last frame.

The static picture is the LAST frame: every other frame carries opacity="0" as an attribute, and a
`@media (prefers-reduced-motion: reduce)` rule turns the animation off, so a reader who asks for reduced motion (or
a viewer without CSS animation) sees the last frame, still and complete.
"""
from __future__ import annotations

from ...inference.jsonschema import validate as schema_validate
from ..spec import Spec, form_schema
from .charts import GENERATORS, STR, describe
from .scales import Fit
from .svg_base import el, head, num, scope_id, text_el

FORMS = ("chart.line", "chart.bar", "chart.scatter")
ANIMATION_DATA = {"type": "object", "additionalProperties": False, "required": ["form", "frames"],
                  "properties": {"form": {"enum": list(FORMS)},
                                 "frames": {"type": "array", "minItems": 2, "maxItems": 60,
                                            "items": {"type": "object", "additionalProperties": False,
                                                      "required": ["label", "data"],
                                                      "properties": {"label": STR, "data": {"type": "object"}}}},
                                 "seconds_per_frame": {"type": "number", "minimum": 0.2, "maximum": 10},
                                 "loop": {"type": "boolean"}}}


def _union_options(form, frames, base):
    o = {k: v for k, v in base.items() if k in ("width", "height", "markers", "mode", "x_labels", "abbreviate")}
    if form == "chart.bar":
        cats = frames[0]["data"]["categories"]
        names = [s["name"] for s in frames[0]["data"]["series"]]
        for f in frames:
            if f["data"]["categories"] != cats or [s["name"] for s in f["data"]["series"]] != names:
                raise Fit("every frame of an animated bar chart needs the same categories and series")
        stacked = base.get("mode") == "stacked"
        vals = []
        for f in frames:
            series = f["data"]["series"]
            if stacked:
                vals += [sum(s["values"][j] or 0 for s in series) for j in range(len(cats))]
            else:
                vals += [v for s in series for v in s["values"] if v is not None]
        o["y_max"] = max(vals + [0])
        if min(vals + [0]) < 0:
            o["y_min"] = min(vals)
        return o
    names = [s["name"] for s in frames[0]["data"]["series"]]
    xs, ys = [], []
    for f in frames:
        if [s["name"] for s in f["data"]["series"]] != names:
            raise Fit("every frame of an animated chart needs the same series, in the same order")
        for s in f["data"]["series"]:
            for x, y in s["points"]:
                if x is not None and y is not None:
                    xs.append(x)
                    ys.append(y)
    if not xs:
        raise Fit("the frames hold no complete points")
    o.update({"x_min": min(xs), "x_max": max(xs), "y_min": min(ys), "y_max": max(ys)})
    return o


def make_animation(spec):
    d, base = spec.data, spec.options
    form = d["form"]
    frames = d["frames"]
    schema = form_schema(form)
    for i, f in enumerate(frames):
        errors = schema_validate(f["data"], schema["data"], f"$.data.frames[{i}].data")
        if errors:
            raise Fit("a frame does not fit its chart: " + "; ".join(errors[:3]))
    options = _union_options(form, frames, base)
    fn = GENERATORS[form][0]
    title = spec.title or "Animated chart"
    sid = scope_id("animation.svg", spec.to_json())
    parts = []
    static = None
    width = height = None
    for i, f in enumerate(frames):
        # A frame is drawn by the chart's own generator, on the shared ranges (not a spec anyone wrote: it is not
        # validated again, its data was validated above).
        frame_spec = Spec(spec=form, title=title, description="", data=f["data"], options=options)
        svg, _notes = fn(frame_spec)
        svg = svg.replace(scope_id(form, frame_spec.to_json()), sid)
        start = svg.index('<rect class="paper"')
        start = svg.index("/>", start) + 2
        cut = svg.index('<g class="plot">')
        body_static, plot = svg[start:cut], svg[cut:-len("</svg>")]
        if static is None:
            static = body_static
            width = float(svg.split('width="', 1)[1].split('"', 1)[0])
            height = float(svg.split('height="', 1)[1].split('"', 1)[0])
            defs = svg[svg.index("<defs>"):svg.index("</defs>") + 7] if "<defs>" in svg else ""
        elif body_static != static:
            raise Fit("the frames do not share their axes and legend (same series, categories and labels needed)")
        label = text_el(width - 20, 28, f["label"], size=15, anchor="end", weight="600", cls="frame-label")
        hidden = i < len(frames) - 1
        parts.append(el("g", (("class", f"frame f{i}"), ("data-label", f["label"]),
                              ("opacity", "0" if hidden else None)), plot + label))
    n = len(frames)
    seconds = float(d.get("seconds_per_frame", 1.0))
    loop = bool(d.get("loop", True))
    css = [f"#{sid} .frame{{animation-duration:{num(n * seconds, 3)}s;animation-timing-function:step-end;"
           f"animation-iteration-count:{'infinite' if loop else '1'};animation-fill-mode:forwards}}"]
    for i in range(n):
        a, b = num(100 * i / n, 4), num(100 * (i + 1) / n, 4)
        if i == 0:
            frames_css = f"0%{{opacity:1}}{b}%{{opacity:0}}100%{{opacity:0}}"
        elif i == n - 1:
            frames_css = f"0%{{opacity:0}}{a}%{{opacity:1}}100%{{opacity:1}}"
        else:
            frames_css = f"0%{{opacity:0}}{a}%{{opacity:1}}{b}%{{opacity:0}}100%{{opacity:0}}"
        css.append(f"#{sid} .f{i}{{animation-name:{sid}-k{i}}}@keyframes {sid}-k{i}{{{frames_css}}}")
    css.append(f"@media (prefers-reduced-motion:reduce){{#{sid} .frame{{animation:none}}}}")
    words = (f"Animated {form.replace('chart.', '')} chart in {n} frames ({frames[0]['label']} to "
             f"{frames[-1]['label']}), {num(seconds, 2)} s each{', looping' if loop else ''}; every frame uses "
             "the same axes. With reduced motion, or without animation, the last frame is shown still.")
    out = head(sid, "animation.svg", width, height, title, describe(spec, words), d, "".join(css),
               defs[len("<defs>"):-len("</defs>")] if defs else "")
    return out + static + "".join(parts) + "</svg>", ()


__all__ = ["ANIMATION_DATA", "FORMS", "make_animation"]
