"""A flow of steps: ordered steps with branches (decisions) and loops, as SVG (top to bottom).

Same reading contract as the other diagrams (graphs.py): steps are `<g class="node">` (start and end as pills,
steps as rectangles, decisions as diamonds), links are `<g class="edge">` with a `<polyline class="edge">` from the
step it leaves to the step it enters, an arrowhead, and the link's label (a branch's "yes" / "no") as
`<text class="edge-label">`, inside a `<g class="link">`; the line runs between the steps' centres, under them. Without links, the steps follow each other in the order given. A link that goes back
up (a loop) leaves its step to the right, runs up the right side of the whole flow, and enters its target from
above, through the gap between layers.
"""
from __future__ import annotations

from .charts import STR
from .graphs import Box, _canvas, _route, layered
from .scales import Fit

ID = {"type": "string", "pattern": r"^[A-Za-z0-9_.:-]{1,64}$"}
DATA = {"type": "object", "additionalProperties": False, "required": ["steps"],
        "properties": {
            "steps": {"type": "array", "minItems": 1, "maxItems": 200,
                      "items": {"type": "object", "additionalProperties": False, "required": ["id", "label"],
                                "properties": {"id": ID, "label": STR,
                                               "kind": {"enum": ["start", "step", "decision", "end"]}}}},
            "links": {"type": "array", "maxItems": 600,
                      "items": {"type": "object", "additionalProperties": False, "required": ["from", "to"],
                                "properties": {"from": ID, "to": ID, "label": STR}}}}}
SHAPES = {"start": "pill", "end": "pill", "decision": "diamond", "step": "rect"}


def make_flow(spec):
    d = spec.data
    steps = d["steps"]
    ids = [s["id"] for s in steps]
    if len(set(ids)) != len(ids):
        raise Fit("step ids must be unique")
    index = {sid: i for i, sid in enumerate(ids)}
    links = d.get("links")
    if links is None:
        links = [{"from": a, "to": b} for a, b in zip(ids, ids[1:])]
    for link in links:
        if link["from"] not in index or link["to"] not in index:
            raise Fit("a link names a step that is not in the list")
    boxes = [Box(s["id"], s["label"], "", SHAPES[s.get("kind", "step")]) for s in steps]
    pairs = [(index[link["from"]], index[link["to"]]) for link in links]
    bends, back, layer = layered(boxes, pairs)
    right = max(b.x + b.w / 2 for b in boxes)
    items, extra, loops = [], [], 0
    for k, (link, (u, v)) in enumerate(zip(links, pairs)):
        a, b = boxes[u], boxes[v]
        if k in back or u == v:
            x = right + 28 + 16 * loops
            loops += 1
            # Out of the source's right side, up the right of the whole flow, then in from above the target
            # (through the gap between layers, where no step sits).
            gap_y = b.y - b.h / 2 - 14 - 4 * (loops - 1)
            entry = b.x + b.w / 4
            pts = [(a.x, a.y), (x, a.y), (x, gap_y), (entry, gap_y),
                   (entry, b.y if b.shape != "diamond" else b.y - b.h / 4)]     # ends inside the target
            extra += [(x + 8, a.y), (x + 8, gap_y)]
        else:
            pts = _route(boxes, u, v, bends.get(k, []))
        items.append((k, pts, "", link.get("label"), b))
    decisions = sum(1 for s in steps if s.get("kind") == "decision")
    words = (f"Flow of {len(steps)} steps ({decisions} decisions) and {len(links)} links, top to bottom"
             + (f"; {loops} links loop back and are drawn around the right side" if loops else "") + ".")
    return _canvas(spec, "flow.steps", boxes, items, directed=True, edge_kinds=[], node_kinds=[], words=words,
                   extra_points=extra), ()


__all__ = ["DATA", "make_flow"]
