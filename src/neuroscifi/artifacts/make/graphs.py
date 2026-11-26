"""Diagrams of relations: node-link graphs and trees, as SVG. Deterministic layouts only.

Reading contract for diagrams: nodes are `<g class="node">` inside `<g class="nodes">`, each holding one shape
(`rect`, `polygon`, `circle`) and a `<text>` label (cut labels carry the full label in `<title>`); edges are
`<g class="edge">` inside `<g class="edges">`, each holding a `<polyline class="edge">` that runs FROM the source
node's boundary TO the target node's boundary, an arrowhead `<path class="arrow">` at the target when the graph is
directed, and the edge's own label as `<text class="edge-label">` when it has one. Edge kinds are told apart by
dash pattern and listed in a legend: `<g class="key edge-kind" data-name="kind">` holding a line with that dash.
Node kinds are told apart by border and fill and listed as `<g class="key node-kind">`.

Layouts:
- layered (directed graphs without cycles; flows break cycles first by reversing the edges a depth-first search in
  input order finds going back): each node's layer is its longest path from a source; edges longer than one layer
  pass through bend points on the layers between; order within layers by eight barycentre sweeps (ties keep input
  order); x by four passes pulling each node towards its neighbours' mean, then packing left to right;
- force (any other graph): Fruchterman-Reingold inside a square frame, from nodes placed on a circle in input
  order, a fixed 300 iterations with linear cooling, sums taken sequentially (the same bytes on any host), then
  boxes pushed apart;
- tree: leaves in depth-first order one slot apart, each parent centred over its children (top to bottom).
"""
from __future__ import annotations

import math

import numpy as np

from .charts import HISTORY, STR, describe, title_block
from .scales import Fit
from .svg_base import EDGE_DASHES, abbreviate, el, head, num, patterns, scope_id, text_el, text_width

LABEL = 12
NODE_H = 30.0
NODE_PAD = 12.0
NODE_MAX_W = 180.0
LAYER_GAP = 64.0
NODE_GAP = 26.0
MAX_NODES = 400
MAX_EDGES = 2000
MAX_CANVAS = 4000

ID = {"type": "string", "pattern": r"^[A-Za-z0-9_.:-]{1,64}$"}
NODE = {"type": "object", "additionalProperties": False, "required": ["id", "label"],
        "properties": {"id": ID, "label": STR, "kind": {"type": "string", "maxLength": 40}}}
EDGE = {"type": "object", "additionalProperties": False, "required": ["from", "to"],
        "properties": {"from": ID, "to": ID, "kind": {"type": "string", "maxLength": 40}, "label": STR}}
NODELINK_DATA = {"type": "object", "additionalProperties": False, "required": ["nodes", "edges"],
                 "properties": {"nodes": {"type": "array", "minItems": 1, "maxItems": MAX_NODES, "items": NODE},
                                "edges": {"type": "array", "maxItems": MAX_EDGES, "items": EDGE},
                                "directed": {"type": "boolean"}}}
TREE_NODE = {"type": "object", "additionalProperties": False, "required": ["id", "label"],
             "properties": {"id": ID, "label": STR, "parent": {"type": ["string", "null"], "maxLength": 64},
                            "kind": {"type": "string", "maxLength": 40}}}
TREE_DATA = {"type": "object", "additionalProperties": False, "required": ["nodes"],
             "properties": {"nodes": {"type": "array", "minItems": 1, "maxItems": MAX_NODES, "items": TREE_NODE}}}
OPTIONS = {"type": "object", "additionalProperties": False, "properties": {
    "layout": {"enum": ["auto", "layered", "force"]},
    "seed": {"type": "integer", "minimum": 0, "maximum": 2 ** 31 - 1},
    "history": HISTORY}}

# Node kind styles: (fill, fill-opacity, stroke-width, stroke-dasharray).
KIND_STYLES = (("var(--nsf-paper)", None, "1.5", None), ("currentColor", "0.16", "1.5", None),
               ("var(--nsf-paper)", None, "3", None), ("var(--nsf-paper)", None, "1.5", "6 3"),
               ("currentColor", "0.3", "1.5", "0.5 3.5"), ("var(--nsf-paper)", None, "1.5", "0.5 3.5"))


# --- node boxes -------------------------------------------------------------------------------------------------

class Box:
    __slots__ = ("id", "label", "shown", "cut", "kind", "w", "h", "x", "y", "shape")

    def __init__(self, nid, label, kind="", shape="rect"):
        self.id, self.label, self.kind, self.shape = nid, label, kind, shape
        self.shown, self.cut = abbreviate(label, NODE_MAX_W - 2 * NODE_PAD, LABEL)
        extra = 1.6 if shape == "diamond" else 1.0
        self.w = max(48.0, (text_width(self.shown, LABEL) + 2 * NODE_PAD) * extra)
        self.h = NODE_H * (1.5 if shape == "diamond" else 1.0)
        self.x = self.y = 0.0

    def boundary(self, tx, ty):
        """The point where the segment from this box's centre towards (tx, ty) leaves its outline."""
        dx, dy = tx - self.x, ty - self.y
        if dx == 0 and dy == 0:
            return self.x, self.y
        hw, hh = self.w / 2, self.h / 2
        if self.shape == "diamond":
            t = 1.0 / (abs(dx) / hw + abs(dy) / hh)
        else:
            t = min(hw / abs(dx) if dx else math.inf, hh / abs(dy) if dy else math.inf)
        return self.x + dx * t, self.y + dy * t


def node_svg(b: Box, kinds: list[str]) -> str:
    style = KIND_STYLES[kinds.index(b.kind) % len(KIND_STYLES)] if b.kind in kinds else KIND_STYLES[0]
    fill, op, sw, dash = style
    common = (("fill", fill), ("fill-opacity", op), ("stroke", "currentColor"), ("stroke-width", sw),
              ("stroke-dasharray", dash))
    if b.shape == "diamond":
        pts = f"{num(b.x)},{num(b.y - b.h / 2)} {num(b.x + b.w / 2)},{num(b.y)} {num(b.x)},{num(b.y + b.h / 2)} " \
              f"{num(b.x - b.w / 2)},{num(b.y)}"
        shape = el("polygon", (("points", pts),) + common)
    else:
        rx = b.h / 2 if b.shape == "pill" else 6
        shape = el("rect", (("x", num(b.x - b.w / 2)), ("y", num(b.y - b.h / 2)), ("width", num(b.w)),
                            ("height", num(b.h)), ("rx", num(rx))) + common)
    label = text_el(b.x, b.y + LABEL * 0.35, b.shown, size=LABEL, anchor="middle", full=b.label if b.cut else None)
    return el("g", (("class", "node"),), shape + label)


def arrow_svg(p, q) -> str:
    """A filled arrowhead at q pointing from p to q."""
    dx, dy = q[0] - p[0], q[1] - p[1]
    n = math.hypot(dx, dy) or 1.0
    ux, uy = dx / n, dy / n
    bx, by = q[0] - ux * 9, q[1] - uy * 9
    a = (bx - uy * 4, by + ux * 4)
    b = (bx + uy * 4, by - ux * 4)
    d = f"M{num(q[0])},{num(q[1])} L{num(a[0])},{num(a[1])} L{num(b[0])},{num(b[1])} Z"
    return el("path", (("class", "arrow"), ("d", d), ("fill", "currentColor")))


def edge_svg(points, *, dash: str, directed: bool, label: str | None) -> str:
    pts = " ".join(f"{num(x)},{num(y)}" for x, y in points)
    body = el("polyline", (("class", "edge"), ("points", pts), ("fill", "none"), ("stroke", "currentColor"),
                           ("stroke-width", "1.4"), ("stroke-dasharray", dash or None), ("stroke-linecap", "round"),
                           ("stroke-linejoin", "round")))
    if directed:
        body += arrow_svg(points[-2], points[-1])
    if label:   # on the longest segment (a loop's label sits on its long side, not where it crosses others)
        i = max(range(len(points) - 1), key=lambda j: (math.hypot(points[j + 1][0] - points[j][0],
                                                                  points[j + 1][1] - points[j][1]), -j))
        mx, my = (points[i][0] + points[i + 1][0]) / 2, (points[i][1] + points[i + 1][1]) / 2
        shown, cut = abbreviate(label, 110, LABEL - 1)
        w = text_width(shown, LABEL - 1) + 8
        body += el("rect", (("class", "label-bg"), ("x", num(mx - w / 2)), ("y", num(my - 8)), ("width", num(w)),
                            ("height", "15"), ("fill", "var(--nsf-paper)")))
        body += text_el(mx, my + 4, shown, size=LABEL - 1, anchor="middle", cls="edge-label",
                        full=label if cut else None)
    return el("g", (("class", "edge"),), body)


def legend_svg(x, y, width, edge_kinds, node_kinds) -> tuple[str, float]:
    keys, cx, cy = [], x, y
    for kind_list, cls in ((edge_kinds, "edge-kind"), (node_kinds, "node-kind")):
        for i, name in enumerate(kind_list):
            shown, cut = abbreviate(name or "no kind given", 180, LABEL - 1)
            w = 34 + text_width(shown, LABEL - 1) + 18
            if cx > x and cx + w > width - 16:
                cx, cy = x, cy + 20
            if cls == "edge-kind":
                glyph = el("line", (("x1", "0"), ("y1", "0"), ("x2", "26"), ("y2", "0"), ("stroke", "currentColor"),
                                    ("stroke-width", "1.4"), ("stroke-dasharray", EDGE_DASHES[i % len(EDGE_DASHES)] or None),
                                    ("stroke-linecap", "round")))
            else:
                fill, op, sw, dash = KIND_STYLES[i % len(KIND_STYLES)]
                glyph = el("rect", (("x", "2"), ("y", "-7"), ("width", "22"), ("height", "14"), ("rx", "3"),
                                    ("fill", fill), ("fill-opacity", op), ("stroke", "currentColor"),
                                    ("stroke-width", sw), ("stroke-dasharray", dash)))
            keys.append(el("g", (("class", f"key {cls}"), ("data-name", name),
                                 ("transform", f"translate({num(cx)},{num(cy)})")),
                           glyph + text_el(32, 4, shown, size=LABEL - 1, full=name if cut else None)))
            cx += w
    if not keys:
        return "", y - 20
    return el("g", (("class", "legend"),), "".join(keys)), cy


# --- layouts ----------------------------------------------------------------------------------------------------

def break_cycles(n: int, edges: list[tuple[int, int]]) -> set[int]:
    """Indexes of edges a depth-first search (input order) finds going back to a node on its path."""
    out_edges: list[list[int]] = [[] for _ in range(n)]
    for k, (u, v) in enumerate(edges):
        out_edges[u].append(k)
    state = [0] * n
    back: set[int] = set()
    for start in range(n):
        if state[start]:
            continue
        stack = [(start, 0)]
        state[start] = 1
        while stack:
            u, i = stack[-1]
            if i < len(out_edges[u]):
                stack[-1] = (u, i + 1)
                k = out_edges[u][i]
                v = edges[k][1]
                if state[v] == 1:
                    back.add(k)
                elif state[v] == 0:
                    state[v] = 1
                    stack.append((v, 0))
            else:
                state[u] = 2
                stack.pop()
    return back


def layered(boxes: list[Box], edges: list[tuple[int, int]]):
    """Place boxes in layers (top to bottom). Returns {edge index: [bend points]} for edges spanning layers."""
    n = len(boxes)
    back = break_cycles(n, edges)
    dag = [(v, u) if k in back else (u, v) for k, (u, v) in enumerate(edges)]
    layer = [0] * n
    preds: list[list[int]] = [[] for _ in range(n)]
    for u, v in dag:
        if u != v:
            preds[v].append(u)
    # Longest path from a source, by repeated relaxation in topological order (Kahn's, ties by index).
    indeg = [0] * n
    succ: list[list[int]] = [[] for _ in range(n)]
    for u, v in dag:
        if u != v:
            indeg[v] += 1
            succ[u].append(v)
    ready = [i for i in range(n) if indeg[i] == 0]
    order = []
    while ready:
        ready.sort()
        u = ready.pop(0)
        order.append(u)
        for v in succ[u]:
            indeg[v] -= 1
            if indeg[v] == 0:
                ready.append(v)
    for u in order:
        for v in succ[u]:
            layer[v] = max(layer[v], layer[u] + 1)
    n_layers = max(layer) + 1
    # Virtual nodes for long edges.
    rows: list[list] = [[] for _ in range(n_layers)]
    for i in range(n):
        rows[layer[i]].append(("n", i))
    chains: dict[int, list] = {}
    for k, (u, v) in enumerate(dag):
        if u == v:
            continue
        a, b = layer[u], layer[v]
        if b - a > 1:
            chain = []
            for lay in range(a + 1, b):
                item = ("v", k, lay)
                rows[lay].append(item)
                chain.append(item)
            chains[k] = chain
    # Neighbour lists over items.
    up: dict = {item: [] for row in rows for item in row}
    down: dict = {item: [] for row in rows for item in row}
    for k, (u, v) in enumerate(dag):
        if u == v:
            continue
        path = [("n", u)] + chains.get(k, []) + [("n", v)]
        for p, q in zip(path, path[1:]):
            down[p].append(q)
            up[q].append(p)
    pos = {item: float(i) for row in rows for i, item in enumerate(row)}
    for sweep in range(8):
        rng = range(1, n_layers) if sweep % 2 == 0 else range(n_layers - 2, -1, -1)
        for lay in rng:
            nbr = up if sweep % 2 == 0 else down
            def key(item, nbr=nbr):
                ps = [pos[p] for p in nbr[item]]
                return (sum(ps) / len(ps) if ps else pos[item], pos[item])
            rows[lay].sort(key=key)
            for i, item in enumerate(rows[lay]):
                pos[item] = float(i)

    def width(item):
        return boxes[item[1]].w if item[0] == "n" else 8.0
    xs: dict = {}
    for row in rows:
        x = 0.0
        for item in row:
            xs[item] = x + width(item) / 2
            x += width(item) + NODE_GAP
        shift = (x - NODE_GAP) / 2
        for item in row:
            xs[item] -= shift
    for _ in range(4):
        for lay, row in enumerate(rows):
            desired = []
            for item in row:
                nb = up[item] + down[item]
                desired.append(sum(xs[p] for p in nb) / len(nb) if nb else xs[item])
            placed = []
            for i, item in enumerate(row):
                x = desired[i]
                if placed:
                    prev_item, prev_x = placed[-1]
                    x = max(x, prev_x + width(prev_item) / 2 + NODE_GAP + width(item) / 2)
                placed.append((item, x))
            shift = (sum(desired) - sum(x for _, x in placed)) / max(1, len(placed))
            for item, x in placed:
                xs[item] = x + shift
    heights = [max([boxes[i[1]].h for i in row if i[0] == "n"] + [NODE_H]) for row in rows]
    ys, y = [], 0.0
    for h in heights:
        ys.append(y + h / 2)
        y += h + LAYER_GAP
    for i, b in enumerate(boxes):
        b.x, b.y = xs[("n", i)], ys[layer[i]]
    bends = {k: [(xs[item], ys[item[2]]) for item in chain] for k, chain in chains.items()}
    return bends, back, layer


def force(boxes: list[Box], edges: list[tuple[int, int]], seed: int = 0, iterations: int = 300):
    """Fruchterman-Reingold, deterministic: circle start (rotated by `seed`), fixed iterations, sequential sums."""
    n = len(boxes)
    area = sum((b.w + NODE_GAP) * (b.h + LAYER_GAP / 2) for b in boxes) * 2.5
    k = math.sqrt(area / n)
    half = math.sqrt(area) / 2                 # the frame positions are kept inside
    radius = min(half * 0.8, k * max(1.0, n / (2 * math.pi)) * 0.6 + 1.0)
    angle0 = (seed % 360) * math.pi / 180
    pos = np.array([[radius * math.cos(angle0 + 2 * math.pi * i / n), radius * math.sin(angle0 + 2 * math.pi * i / n)]
                    for i in range(n)], dtype=np.float64)
    src = np.array([u for u, v in edges if u != v], dtype=np.int64)
    dst = np.array([v for u, v in edges if u != v], dtype=np.int64)
    t0 = half * 0.2
    for it in range(iterations):
        delta = pos[:, None, :] - pos[None, :, :]
        dist = np.sqrt(delta[..., 0] * delta[..., 0] + delta[..., 1] * delta[..., 1]) + 0.01
        rep = (k * k) / (dist * dist)
        disp = np.stack([np.cumsum(delta[..., 0] * rep, axis=1)[:, -1],
                         np.cumsum(delta[..., 1] * rep, axis=1)[:, -1]], axis=1)
        if len(src):
            d = pos[src] - pos[dst]
            dl = np.sqrt(d[:, 0] * d[:, 0] + d[:, 1] * d[:, 1]) + 0.01
            f = d * (dl / k)[:, None]
            for e in range(len(src)):          # sequential accumulation (same order on every host)
                disp[src[e]] -= f[e]
                disp[dst[e]] += f[e]
        length = np.sqrt(disp[:, 0] * disp[:, 0] + disp[:, 1] * disp[:, 1]) + 1e-9
        t = t0 * (1 - it / iterations)
        step = np.minimum(length, t) / length
        pos = np.clip(pos + disp * step[:, None], -half, half)
        pos = np.round(pos, 6)                 # keeps tiny platform differences from compounding
    for i, b in enumerate(boxes):
        b.x, b.y = float(pos[i, 0]), float(pos[i, 1])
    separate(boxes)


def separate(boxes: list[Box], passes: int = 60):
    """Push overlapping boxes apart along the axis of least overlap (pairs in index order)."""
    for _ in range(passes):
        moved = False
        for i in range(len(boxes)):
            a = boxes[i]
            for j in range(i + 1, len(boxes)):
                b = boxes[j]
                ox = (a.w + b.w) / 2 + 14 - abs(a.x - b.x)
                oy = (a.h + b.h) / 2 + 14 - abs(a.y - b.y)
                if ox > 0 and oy > 0:
                    moved = True
                    if ox < oy:
                        s = ox / 2 * (1 if a.x >= b.x else -1)
                        a.x, b.x = a.x + s, b.x - s
                    else:
                        s = oy / 2 * (1 if a.y >= b.y else -1)
                        a.y, b.y = a.y + s, b.y - s
        if not moved:
            return


def tidy_tree(boxes: list[Box], parent: list[int | None]):
    n = len(boxes)
    children: list[list[int]] = [[] for _ in range(n)]
    roots = []
    for i, p in enumerate(parent):
        (children[p] if p is not None else roots).append(i)
    slot = max(b.w for b in boxes) + NODE_GAP * 0.6
    depth = [0] * n
    next_leaf = [0.0]
    xs = [0.0] * n
    order = []
    stack = [(r, 0, False) for r in reversed(roots)]
    while stack:
        u, d, done = stack.pop()
        if done:
            xs[u] = (xs[children[u][0]] + xs[children[u][-1]]) / 2 if children[u] else xs[u]
            order.append(u)
            continue
        depth[u] = d
        if not children[u]:
            xs[u] = next_leaf[0] * slot
            next_leaf[0] += 1
            order.append(u)
            continue
        stack.append((u, d, True))
        for c in reversed(children[u]):
            stack.append((c, d + 1, False))
    for i, b in enumerate(boxes):
        b.x, b.y = xs[i], depth[i] * (NODE_H + LAYER_GAP * 0.8)
    return children


# --- pictures ---------------------------------------------------------------------------------------------------

def _canvas(spec, form, boxes, edge_items, *, directed, edge_kinds, node_kinds, words, extra_points=()):
    """Translate the layout into a canvas under the title and above the legend, and write the SVG."""
    xs = [b.x - b.w / 2 for b in boxes] + [p[0] for p in extra_points]
    xe = [b.x + b.w / 2 for b in boxes] + [p[0] for p in extra_points]
    ys = [b.y - b.h / 2 for b in boxes] + [p[1] for p in extra_points]
    ye = [b.y + b.h / 2 for b in boxes] + [p[1] for p in extra_points]
    for _k, pts, _dash, label in edge_items:
        for x, y in pts:
            xs.append(x - (60 if label else 0))
            xe.append(x + (60 if label else 0))
            ys.append(y)
            ye.append(y)
    minx, maxx, miny, maxy = min(xs), max(xe), min(ys), max(ye)
    title = spec.title or "Diagram"
    W = max(360.0, maxx - minx + 48, text_width(title, 16) + 48)
    title_svg, top = title_block(W, title)
    dx = (W - (maxx - minx)) / 2 - minx
    dy = top + 16 - miny
    for b in boxes:
        b.x += dx
        b.y += dy
    edges_out = []
    for _k, pts, dash, label in edge_items:
        moved = [(x + dx, y + dy) for x, y in pts]
        edges_out.append(edge_svg(moved, dash=dash, directed=directed, label=label))
    legend, bottom = legend_svg(16, maxy + dy + 30, W, edge_kinds, node_kinds)
    H = bottom + 24
    if W > MAX_CANVAS or H > MAX_CANVAS:
        raise Fit(f"the diagram needs {int(W)} x {int(H)} px, more than {MAX_CANVAS}: show fewer nodes")
    sid = scope_id(form, spec.to_json())
    body = (title_svg + el("g", (("class", "edges"),), "".join(edges_out))
            + el("g", (("class", "nodes"),), "".join(node_svg(b, node_kinds) for b in boxes)) + legend)
    return head(sid, form, W, H, title, describe(spec, words), spec.data, defs=patterns(sid)) + body + "</svg>"


def _route(boxes, u, v, bends):
    a, b = boxes[u], boxes[v]
    first = bends[0] if bends else (b.x, b.y)
    last = bends[-1] if bends else (a.x, a.y)
    return [a.boundary(*first)] + list(bends) + [b.boundary(*last)]


def _self_loop(b: Box):
    x, y = b.x + b.w / 2, b.y
    return [(x, y - 6), (x + 22, y - 18), (x + 22, y + 18), (x, y + 6)]


def kinds_of(items, key="kind"):
    out = []
    for it in items:
        k = it.get(key, "")
        if k not in out:
            out.append(k)
    return out


def make_nodelink(spec):
    d, o = spec.data, spec.options
    nodes, edges = d["nodes"], d["edges"]
    ids = [n["id"] for n in nodes]
    if len(set(ids)) != len(ids):
        raise Fit("node ids must be unique")
    index = {nid: i for i, nid in enumerate(ids)}
    for e in edges:
        if e["from"] not in index or e["to"] not in index:
            raise Fit("an edge names a node that is not in the list")
    directed = bool(d.get("directed", True))
    boxes = [Box(n["id"], n["label"], n.get("kind", "")) for n in nodes]
    pairs = [(index[e["from"]], index[e["to"]]) for e in edges]
    layout = o.get("layout", "auto")
    cyclic = bool(break_cycles(len(boxes), pairs))
    use_layered = layout == "layered" or (layout == "auto" and directed and not cyclic)
    bends: dict[int, list] = {}
    if use_layered:
        bends, _back, _layer = layered(boxes, pairs)
    else:
        force(boxes, pairs, int(o.get("seed", 0)))
    edge_kinds = kinds_of(edges)
    node_kinds = kinds_of(nodes)
    show_edge_kinds = edge_kinds if edge_kinds != [""] else []
    show_node_kinds = node_kinds if node_kinds != [""] else []
    items = []
    for k, (e, (u, v)) in enumerate(zip(edges, pairs)):
        pts = _self_loop(boxes[u]) if u == v else _route(boxes, u, v, bends.get(k, []))
        dash = EDGE_DASHES[edge_kinds.index(e.get("kind", "")) % len(EDGE_DASHES)]
        items.append((k, pts, dash, e.get("label")))
    words = (f"{'Directed' if directed else 'Undirected'} graph of {len(nodes)} nodes and {len(edges)} edges, drawn "
             f"with a {'layered' if use_layered else 'force-directed'} layout"
             + (f"; edge kinds by dash: {', '.join(k or '(none)' for k in edge_kinds)}" if show_edge_kinds else "")
             + (f"; node kinds by border and fill: {', '.join(k or '(none)' for k in node_kinds)}"
                if show_node_kinds else "") + ".")
    notes = []
    if len(edge_kinds) > len(EDGE_DASHES) or len(node_kinds) > len(KIND_STYLES):
        notes.append("more kinds than distinguishable styles: some look alike")
    return _canvas(spec, "graph.nodelink", boxes, items, directed=directed, edge_kinds=show_edge_kinds,
                   node_kinds=show_node_kinds, words=words), tuple(notes)


def make_tree(spec):
    d = spec.data
    nodes = d["nodes"]
    ids = [n["id"] for n in nodes]
    if len(set(ids)) != len(ids):
        raise Fit("node ids must be unique")
    index = {nid: i for i, nid in enumerate(ids)}
    parent = []
    for n in nodes:
        p = n.get("parent")
        if p is not None and p not in index:
            raise Fit("a node names a parent that is not in the list")
        parent.append(index[p] if p is not None else None)
    if sum(1 for p in parent if p is None) != 1:
        raise Fit("a tree has exactly one root (one node without a parent)")
    for i in range(len(parent)):          # no cycles: every node reaches the root
        seen, j = set(), i
        while parent[j] is not None:
            if j in seen:
                raise Fit("the parent links form a cycle")
            seen.add(j)
            j = parent[j]
    boxes = [Box(n["id"], n["label"], n.get("kind", "")) for n in nodes]
    children = tidy_tree(boxes, parent)
    items = []
    for c, p in enumerate(parent):
        if p is None:
            continue
        a, b = boxes[p], boxes[c]
        mid = (a.y + a.h / 2 + b.y - b.h / 2) / 2
        items.append((c, [(a.x, a.y + a.h / 2), (a.x, mid), (b.x, mid), (b.x, b.y - b.h / 2)], "", None))
    node_kinds = kinds_of(nodes)
    depth = max(int(round(b.y / (NODE_H + LAYER_GAP * 0.8))) for b in boxes) + 1
    leaves = sum(1 for ch in children if not ch)
    words = (f"Tree of {len(nodes)} nodes in {depth} levels with {leaves} leaves; the root "
             f"({nodes[parent.index(None)]['label'][:60]}) is at the top and each line runs from a parent down to "
             "its child.")
    return _canvas(spec, "graph.tree", boxes, items, directed=False, edge_kinds=[],
                   node_kinds=node_kinds if node_kinds != [""] else [], words=words), ()


__all__ = ["Box", "KIND_STYLES", "NODELINK_DATA", "OPTIONS", "TREE_DATA", "break_cycles", "edge_svg", "force",
           "kinds_of", "layered", "make_nodelink", "make_tree", "node_svg", "separate", "tidy_tree"]
