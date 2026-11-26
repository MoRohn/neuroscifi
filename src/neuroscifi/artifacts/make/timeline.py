"""Timeline: events (instants) and spans (intervals) on a time axis, in lanes, as SVG.

Reading contract (as for charts): `<g class="axis x">` holds the time ticks (ISO 8601) or numeric ticks;
`<g class="axis y">` holds one tick per lane at the lane's marker line, labelled with the lane's name; each lane's
marks are in `<g class="series" data-name="lane">` inside `<g class="plot">`: an event is `<circle class="mark">`
centred on its instant on the lane's marker line; a span is `<rect class="mark">` from its start to its end, below
the marker line and above the next lane's labels. Labels are `<text class="label">` near their marks.

Layout rules: event labels sit above the marker line in up to four rows, each label in the first row where it does
not overlap; a label that fits no row is cut to 12 characters, and if it still fits none it is shown only as the
mark's own <title> (the description counts them). Spans that overlap in time take separate rows.
"""
from __future__ import annotations

from .charts import (FLAG, HISTORY, STR, TICK_SIZE, UNIT, axis_label, describe, plan_x_continuous, title_block,
                     value_words)
from .scales import Fit, Scale
from .svg_base import GRID, abbreviate, el, esc, flag_color, flag_of, head, num, scope_id, text_el, text_width

LABEL = 11
ROW = 14
MAX_ROWS = 4
SPAN_ROW = 20
SPAN_H = 14

EVENT = {"type": "object", "additionalProperties": False, "required": ["lane", "at", "label"],
         "properties": {"lane": STR, "at": {"type": "number"}, "label": STR, "flag": FLAG}}
SPAN = {"type": "object", "additionalProperties": False, "required": ["lane", "start", "end", "label"],
        "properties": {"lane": STR, "start": {"type": "number"}, "end": {"type": "number"}, "label": STR, "flag": FLAG}}
DATA = {"type": "object", "additionalProperties": False, "required": ["x"],
        "properties": {"x": {"type": "object", "additionalProperties": False, "required": ["label"],
                             "properties": {"label": STR, "unit": UNIT, "scale": {"enum": ["time", "linear"]}}},
                       "lanes": {"type": "array", "maxItems": 40, "items": STR},
                       "events": {"type": "array", "maxItems": 2000, "items": EVENT},
                       "spans": {"type": "array", "maxItems": 2000, "items": SPAN}}}
OPTIONS = {"type": "object", "additionalProperties": False, "properties": {
    "width": {"type": "integer", "minimum": 320, "maximum": 3000},
    "x_labels": {"enum": ["auto", "horizontal", "rotated"]},
    "x_tick_every": {"type": "integer", "minimum": 1, "maximum": 50},
    "history": HISTORY}}


def _rows(items, size):
    """Assign each (x_centre, label) to the first of MAX_ROWS rows where its box clears the row's last box."""
    ends = [-1e18] * MAX_ROWS
    out = []
    for x, label in items:
        placed = None
        for shown in (label, abbreviate(label, text_width("M", size) * 12, size)[0]):
            w = text_width(shown, size)
            for r in range(MAX_ROWS):
                if x - w / 2 >= ends[r] + 6:
                    ends[r] = x + w / 2
                    placed = (r, shown)
                    break
            if placed:
                break
        out.append(placed)
    return out


def make_timeline(spec):
    d, o = spec.data, spec.options
    events, spans = list(d.get("events", [])), list(d.get("spans", []))
    if not events and not spans:
        raise Fit("a timeline needs at least one event or span")
    for s in spans:
        if s["end"] < s["start"]:
            raise Fit(f"the span {s['label'][:40]!r} ends before it starts")
    kind = d["x"].get("scale", "time")
    lanes = list(d.get("lanes") or [])
    for item in events + spans:
        if item["lane"] not in lanes:
            if d.get("lanes"):
                raise Fit(f"lane {item['lane'][:40]!r} is not in the list of lanes")
            lanes.append(item["lane"])
    times = [e["at"] for e in events] + [s["start"] for s in spans] + [s["end"] for s in spans]
    lo, hi = min(times), max(times)
    pad = (hi - lo) * 0.03 or (86400 if kind == "time" else 1)
    lo, hi = lo - pad, hi + pad

    W = float(o.get("width", 760))
    sid = scope_id("timeline", spec.to_json())
    title = spec.title or f"Timeline of {d['x']['label']}"
    title_svg, top = title_block(W, title)
    lane_w = max(text_width(n, LABEL) for n in lanes) if lanes else 0
    left = min(180.0, lane_w + 24)
    x0, x1 = left + 8, W - 24
    if x1 - x0 < 120:
        raise Fit("the timeline is too narrow: make it wider (options.width)")
    xt, xa = plan_x_continuous(kind, lo, hi, x0, x1, o, nice=(kind != "time"))
    for _ in range(3):      # long end tick labels (a date and a time) must not run off the canvas: narrow the axis
        if not xa.labels:
            break
        widths = [text_width(s, TICK_SIZE) for s in xa.labels]
        # horizontal labels are centred on their tick; rotated ones (45 degrees, anchored at their end) run left
        left_of = [w * 0.7072 if xa.rotated else w / 2 for w in widths]
        right_of = [8.0 if xa.rotated else w / 2 for w in widths]
        over = max(p + r for p, r in zip(xa.positions, right_of)) - (W - 4)
        under = 4 - min(p - h for p, h in zip(xa.positions, left_of))
        if over <= 0 and under <= 0:
            break
        x0, x1 = x0 + max(0.0, under), x1 - max(0.0, over)
        if x1 - x0 < 120:
            raise Fit("the timeline is too narrow for its date labels: make it wider (options.width)")
        xt, xa = plan_x_continuous(kind, lo, hi, x0, x1, o, nice=(kind != "time"))
    sx = Scale(xt.lo, xt.hi, x0, x1)

    # Per lane: event label rows, then the marker line, then span rows.
    lane_parts, y = [], top + 8
    hidden = 0
    lane_ticks = []
    for name in lanes:
        evs = sorted((e for e in events if e["lane"] == name), key=lambda e: (e["at"], e["label"]))
        sps = sorted((s for s in spans if s["lane"] == name), key=lambda s: (s["start"], s["end"], s["label"]))
        placed = _rows([(sx(e["at"]), e["label"]) for e in evs], LABEL)
        nrows = max([p[0] + 1 for p in placed if p] + [0])
        span_rows: list[float] = []
        span_row_of = []
        for s in sps:
            a = sx(s["start"])
            for r, end in enumerate(span_rows):
                if a >= end + 4:
                    span_rows[r] = sx(s["end"]) + 4 + _span_label_room(s, sx)
                    span_row_of.append(r)
                    break
            else:
                span_rows.append(sx(s["end"]) + 4 + _span_label_room(s, sx))
                span_row_of.append(len(span_rows) - 1)
        y_mark = y + nrows * ROW + (10 if evs else 4)
        body = []
        for e, p in zip(evs, placed):
            flag = flag_of(e.get("flag"))
            c = flag_color(flag)
            ex = sx(e["at"])
            mark_title = "" if p else el("title", (), esc(e["label"]))
            body.append(el("circle", (("class", "mark" + (f" {flag}" if flag else "")), ("cx", num(ex)),
                                      ("cy", num(y_mark)), ("r", "4.5"), ("fill", c)), mark_title or None))
            if p:
                r, shown = p
                ly = y_mark - 10 - (nrows - 1 - r) * ROW
                body.append(el("line", (("x1", num(ex)), ("y1", num(ly + 3)), ("x2", num(ex)), ("y2", num(y_mark - 5)),
                                        ("stroke", "currentColor"), ("stroke-opacity", num(GRID * 2)))))
                body.append(text_el(ex, ly, shown, size=LABEL, anchor="middle", cls="label",
                                    full=e["label"] if shown != e["label"] else None))
            else:
                hidden += 1
        for s, r in zip(sps, span_row_of):
            flag = flag_of(s.get("flag"))
            c = flag_color(flag)
            a, b = sx(s["start"]), sx(s["end"])
            ry = y_mark + 8 + r * SPAN_ROW
            body.append(el("rect", (("class", "mark" + (f" {flag}" if flag else "")), ("x", num(a)), ("y", num(ry)),
                                    ("width", num(max(b - a, 0.0))), ("height", num(SPAN_H)), ("fill", c),
                                    ("fill-opacity", "0.12"), ("stroke", c), ("stroke-width", "1.25"))))
            shown, cut = abbreviate(s["label"], max(40.0, x1 + 20 - b - 6), LABEL)
            inside = text_width(s["label"], LABEL) + 8 <= b - a
            if inside:
                body.append(text_el(a + 5, ry + 10.5, s["label"], size=LABEL - 1, cls="label"))
            else:
                anchor_x = b + 5 if b + 5 + text_width(shown, LABEL - 1) <= W - 4 else a - 5
                body.append(text_el(anchor_x, ry + 10.5, shown, size=LABEL - 1, cls="label",
                                    anchor="start" if anchor_x > b else "end", full=s["label"] if cut else None))
        lane_bottom = y_mark + 8 + len(span_rows) * SPAN_ROW + 10
        lane_parts.append(el("line", (("class", "lane"), ("x1", num(x0)), ("y1", num(y_mark)), ("x2", num(x1)),
                                      ("y2", num(y_mark)), ("stroke", "currentColor"), ("stroke-opacity", num(GRID * 1.6)))))
        lane_parts.append(el("g", (("class", "series"), ("data-name", name)), "".join(body)))
        shown, cut = abbreviate(name, left - 16, LABEL)
        lane_ticks.append(el("g", (("class", "tick"), ("transform", f"translate({num(x0)},{num(y_mark)})")),
                             text_el(-10, LABEL * 0.35, shown, size=LABEL, anchor="end", weight="600",
                                     full=name if cut else None)))
        y = lane_bottom
    y1 = y + 4
    H = y1 + xa.height() + 30
    grid = "".join(el("line", (("x1", num(px)), ("y1", num(top + 4)), ("x2", num(px)), ("y2", num(y1)),
                               ("stroke", "currentColor"), ("stroke-opacity", num(GRID))))
                   for px in xa.positions)
    xaxis = xa.svg(y1, x0, x1, axis_label(d["x"]), H - 12)
    yaxis = el("g", (("class", "axis y"),), "".join(lane_ticks))
    plot = el("g", (("class", "plot"),), "".join(lane_parts))
    words = (f"Timeline of {len(events)} events and {len(spans)} spans in {len(lanes)} lanes "
             f"({', '.join(lanes[:8])}{', ...' if len(lanes) > 8 else ''}), from "
             f"{value_words(min(times), kind)} to {value_words(max(times), kind)}.")
    if hidden:
        words += f" {hidden} event labels did not fit and are shown on their marks only (hover)."
    body = title_svg + el("g", (("class", "grid"),), grid) + yaxis + xaxis + plot
    return head(sid, "timeline", W, H, title, describe(spec, words), d) + body + "</svg>", ()


def _span_label_room(s, sx):
    """Room a span's label takes to the right of the span when it does not fit inside it."""
    need = text_width(s["label"], LABEL - 1) + 8
    return need if need > sx(s["end"]) - sx(s["start"]) else 0


__all__ = ["DATA", "OPTIONS", "make_timeline"]
