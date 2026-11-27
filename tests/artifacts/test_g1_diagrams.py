"""Lane G1: timeline, node-link graph (layered and force), tree and flow of steps; recovered and wrong ones caught."""
import copy
import datetime
import re
import xml.etree.ElementTree as ET

import pytest

import g1_support as S

D = 86400
T0 = 1767225600          # 2026-01-01T00:00:00Z

TIMELINE = {"x": {"label": "Date", "scale": "time"}, "lanes": ["Design", "Build", "Review"],
            "events": [{"lane": "Design", "at": T0 + 3 * D, "label": "Kick-off"},
                       {"lane": "Design", "at": T0 + 20 * D, "label": "Sketches agreed"},
                       {"lane": "Build", "at": T0 + 45 * D, "label": "First working version"},
                       {"lane": "Build", "at": T0 + 47 * D, "label": "Demo to owner"},
                       {"lane": "Review", "at": T0 + 80 * D, "label": "Release", "flag": "warning"}],
            "spans": [{"lane": "Design", "start": T0 + 3 * D, "end": T0 + 25 * D, "label": "Research"},
                      {"lane": "Build", "start": T0 + 20 * D, "end": T0 + 70 * D, "label": "Implementation"},
                      {"lane": "Build", "start": T0 + 60 * D, "end": T0 + 75 * D, "label": "Hardening"},
                      {"lane": "Review", "start": T0 + 70 * D, "end": T0 + 85 * D, "label": "Review"}]}
DAG = {"directed": True,
       "nodes": [{"id": "q", "label": "Question"}, {"id": "s", "label": "Search the web"},
                 {"id": "r", "label": "Read sources"}, {"id": "c", "label": "Extract claims"},
                 {"id": "m", "label": "Memory"}, {"id": "a", "label": "Write the answer"},
                 {"id": "g", "label": "Sentence guard", "kind": "check"}, {"id": "o", "label": "Owner"}],
       "edges": [{"from": "q", "to": "s"}, {"from": "s", "to": "r"}, {"from": "r", "to": "c"},
                 {"from": "c", "to": "a"}, {"from": "m", "to": "a", "kind": "recall"},
                 {"from": "q", "to": "m", "kind": "recall"}, {"from": "a", "to": "g"}, {"from": "g", "to": "o"},
                 {"from": "c", "to": "m", "kind": "store", "label": "new claims"}]}
CYCLIC = {"directed": False, "nodes": [{"id": f"n{i}", "label": f"Paper {chr(65 + i)}"} for i in range(12)],
          "edges": [{"from": f"n{i}", "to": f"n{(i * 5 + 3) % 12}"} for i in range(12)]}
TREE = {"nodes": [{"id": "root", "label": "neuroscifi", "parent": None}, {"id": "src", "label": "src", "parent": "root"},
                  {"id": "tests", "label": "tests", "parent": "root"}, {"id": "art", "label": "artifacts", "parent": "src"},
                  {"id": "make", "label": "make", "parent": "art"}, {"id": "check", "label": "check", "parent": "art"},
                  {"id": "t1", "label": "test_g1_line.py", "parent": "tests"}]}
FLOW = {"steps": [{"id": "s", "label": "Spec arrives", "kind": "start"}, {"id": "m", "label": "Make the picture"},
                  {"id": "c", "label": "Check it"}, {"id": "p", "label": "Passed?", "kind": "decision"},
                  {"id": "r", "label": "Repair the spec"}, {"id": "e", "label": "Show it", "kind": "end"}],
        "links": [{"from": "s", "to": "m"}, {"from": "m", "to": "c"}, {"from": "c", "to": "p"},
                  {"from": "p", "to": "e", "label": "yes"}, {"from": "p", "to": "r", "label": "no"},
                  {"from": "r", "to": "m", "label": "again"}]}

CASES = {"timeline": ("timeline", TIMELINE), "dag": ("graph.nodelink", DAG), "force": ("graph.nodelink", CYCLIC),
         "tree": ("graph.tree", TREE), "flow": ("flow.steps", FLOW)}


def case(name, **options):
    form, data = CASES[name]
    return S.spec(form, f"A {name}", copy.deepcopy(data), options)


@pytest.mark.parametrize("name", list(CASES))
def test_each_diagram_is_deterministic_and_carries_everything(name):
    s = case(name)
    a, b = S.made(s), S.made(s)
    assert a.bytes == b.bytes
    v = S.verdict(a.bytes, s)
    assert v.ok, [(f.rule, f.detail) for f in v.technical if not f.passed]
    assert S.finding(v, "values_recovered").measure == 1.0


def test_layered_layout_puts_every_edge_downwards():
    root = S.tree(S.made(case("dag")).bytes)
    for line in root.iter(S.q("polyline")):
        if "edge" in S.classes(line):
            ys = [float(v) for v in re.findall(r"-?\d+(?:\.\d+)?", line.get("points"))][1::2]
            assert ys[-1] > ys[0]
    keys = [g.get("data-name") for g in root.iter(S.q("g")) if {"key", "edge-kind"} <= S.classes(g)]
    assert keys == ["recall", "store"]                      # edge kinds by dash, in a legend; untyped: no entry
    nkeys = [g.get("data-name") for g in root.iter(S.q("g")) if {"key", "node-kind"} <= S.classes(g)]
    assert nkeys == ["check"]
    assert "no kind given" not in S.made(case("dag")).bytes.decode()


def test_untyped_edges_are_solid_and_have_no_legend():
    g = copy.deepcopy(DAG)
    for e in g["edges"]:
        e.pop("kind", None)
    for n in g["nodes"]:
        n.pop("kind", None)
    s = S.spec("graph.nodelink", "Untyped", g)
    root = S.tree(S.made(s).bytes)
    assert not [e for e in root.iter(S.q("g")) if "key" in S.classes(e)]          # no legend at all
    lines = [e for e in root.iter(S.q("polyline")) if "edge" in S.classes(e)]
    assert lines and all(e.get("stroke-dasharray") is None for e in lines)         # the plain (solid) style
    mixed = copy.deepcopy(DAG)
    mixed["edges"][0]["kind"] = "first"                     # a typed kind before any untyped one: untyped stays solid
    root = S.tree(S.made(S.spec("graph.nodelink", "Mixed", mixed)).bytes)
    untyped = [e for e in root.iter(S.q("polyline")) if "edge" in S.classes(e)][1]
    assert untyped.get("stroke-dasharray") is None


def _as_paths_and_lines(data):
    """The same edges drawn as a path (M/L/C commands, relative too) and as a straight line: the reading is the same."""
    root = S.tree(data)
    parents = {c: p for p in root.iter() for c in p}
    for k, e in enumerate([e for e in root.iter(S.q("polyline")) if "edge" in S.classes(e)]):
        pts = [tuple(float(v) for v in p.split(",")) for p in e.get("points").split()]
        (x0, y0), (x1, y1) = pts[0], pts[-1]
        if k % 2:
            new = ET.Element(S.q("line"), {"class": "edge", "x1": str(x0), "y1": str(y0), "x2": str(x1), "y2": str(y1),
                                            "stroke": "currentColor", "stroke-dasharray": e.get("stroke-dasharray", "")})
        else:
            d = f"M{x0},{y0} c 0,0 {(x1 - x0) / 2},{(y1 - y0) / 2} {x1 - x0},{y1 - y0}"
            new = ET.Element(S.q("path"), {"class": "edge", "d": d, "fill": "none", "stroke": "currentColor",
                                            "stroke-dasharray": e.get("stroke-dasharray", "")})
        if not new.get("stroke-dasharray"):
            del new.attrib["stroke-dasharray"]
        p = parents[e]
        p.insert(list(p).index(e), new)
        p.remove(e)
    return S.to_bytes(root)


def test_edges_drawn_as_paths_or_lines_read_the_same():
    s = case("dag")
    v = S.verdict(_as_paths_and_lines(S.made(s).bytes), s)
    assert v.ok, S.failed(v)
    assert S.finding(v, "values_recovered").measure == 1.0


def test_tree_outline_reads_depth_first_in_source_order():
    s = case("tree")
    v = S.verdict(S.made(s).bytes, s)
    assert v.ok and S.finding(v, "outline_order").passed
    # A wide tree (lines spread further sideways than down): it still grows downwards, siblings read left to right.
    wide = {"nodes": [{"id": "r", "label": "root", "parent": None}, {"id": "a", "label": "A", "parent": "r"},
                      {"id": "b", "label": "B", "parent": "r"}]
            + [{"id": f"a{i}", "label": f"leaf {i}", "parent": "a"} for i in range(10)]}
    s = S.spec("graph.tree", "Wide", wide)
    v = S.verdict(S.made(s).bytes, s)
    assert v.ok and S.finding(v, "outline_order").passed, S.failed(v)


def test_force_layout_differs_by_seed_and_keeps_nodes_apart():
    a = S.made(case("force")).bytes
    b = S.made(case("force", seed=90)).bytes
    assert a != b
    v = S.verdict(b, case("force", seed=90))
    assert v.ok and S.finding(v, "nodes_apart").passed


def test_timeline_hides_no_label_silently():
    data = copy.deepcopy(TIMELINE)
    data["events"] = [{"lane": "Design", "at": T0 + i * 3600, "label": f"Event number {i}"} for i in range(12)]
    s = S.spec("timeline", "Crowded", data)
    m = S.made(s)
    desc = S.tree(m.bytes).find(S.q("desc")).text
    assert "shown on their marks only" in desc
    v = S.verdict(m.bytes, s)
    assert v.ok and S.finding(v, "labels_present").passed


@pytest.mark.parametrize("span,n,width", [(60, 2, 400), (3600, 5, None), (2 * D, 2, None), (0, 1, None)])
def test_short_timelines_keep_their_date_and_time_ticks_on_the_canvas(span, n, width):
    data = {"x": {"label": "Date", "scale": "time"},
            "events": [{"lane": "A", "at": T0 + span * i / max(1, n - 1), "label": f"Event {i}"} for i in range(n)]}
    s = S.spec("timeline", "Short", data, {"width": width} if width else {})
    v = S.verdict(S.made(s).bytes, s)
    assert v.ok and S.finding(v, "text_inside_canvas").passed, S.failed(v)
    assert S.finding(v, "values_recovered").measure == 1.0


def test_bad_structures_are_refused():
    t = copy.deepcopy(TREE)
    t["nodes"][1]["parent"] = "make"                         # a cycle
    t["nodes"][0]["parent"] = None
    with pytest.raises(ValueError):
        S.made(S.spec("graph.tree", "Cycle", t))
    t2 = copy.deepcopy(TREE)
    t2["nodes"][1]["parent"] = None                          # two roots
    with pytest.raises(ValueError, match="one root"):
        S.made(S.spec("graph.tree", "Two roots", t2))
    g = copy.deepcopy(DAG)
    g["edges"].append({"from": "q", "to": "nowhere"})
    with pytest.raises(ValueError):
        S.made(S.spec("graph.nodelink", "Dangling", g))
    tl = copy.deepcopy(TIMELINE)
    tl["spans"][0]["end"] = tl["spans"][0]["start"] - 1
    with pytest.raises(ValueError, match="ends before"):
        S.made(S.spec("timeline", "Backwards", tl))


def test_long_node_labels_keep_their_full_text_and_nodes_sit_at_their_group_origin():
    g = copy.deepcopy(DAG)
    g["nodes"][0]["label"] = "A question that is far too long to fit inside one ordinary node box"
    s = S.spec("graph.nodelink", "Long", g)
    m = S.made(s)
    root = S.tree(m.bytes)
    nodes = [e for e in root.iter(S.q("g")) if "node" in S.classes(e)]
    assert next(nodes[0].iter(S.q("text"))).text == g["nodes"][0]["label"] and "…" not in m.bytes.decode()
    for n in nodes:                       # the group's origin is the centre; its first shape is centred there
        assert n.get("transform").startswith("translate(")
        shape = list(n)[0]
        if shape.tag == S.q("rect"):
            assert float(shape.get("x")) == -float(shape.get("width")) / 2
    assert not [e for e in root.iter(S.q("g")) if "edge" in S.classes(e)]     # only the lines carry class edge
    assert S.verdict(m.bytes, s).ok


# --- wrong artifacts --------------------------------------------------------------------------------------------

def _move_event(data):
    root = S.tree(data)
    c = next(e for e in root.iter(S.q("circle")) if "mark" in S.classes(e))
    c.set("cx", str(float(c.get("cx")) + 40))
    return S.to_bytes(root)


def _shift_dates(data):
    """Every time tick claims a date 9 days later (about a tenth of the span): a time scale that is off."""
    root = S.tree(data)
    for g in S.groups(root, "axis", "x"):
        for t in g.iter(S.q("text")):
            if t.text and re.fullmatch(r"\d{4}-\d{2}-\d{2}", t.text):
                day = datetime.date.fromisoformat(t.text) + datetime.timedelta(days=9)
                t.text = day.isoformat()
    return S.to_bytes(root)


def _reverse_edge(data):
    root = S.tree(data)
    line = next(e for e in root.iter(S.q("polyline")) if "edge" in S.classes(e))
    pts = re.findall(r"-?\d+(?:\.\d+)?,-?\d+(?:\.\d+)?", line.get("points"))
    line.set("points", " ".join(reversed(pts)))
    return S.to_bytes(root)


def _drop_edge(data):
    root = S.tree(data)
    edges = next(g for g in root.iter(S.q("g")) if "edges" in S.classes(g))
    edges.remove(list(edges)[0])                       # one <g class="link">
    return S.to_bytes(root)


def _rename_node(data):
    root = S.tree(data)
    node = next(g for g in root.iter(S.q("g")) if "node" in S.classes(g))
    next(node.iter(S.q("text"))).text = "Somebody else"
    return S.to_bytes(root)


def _stack_nodes(data):
    root = S.tree(data)
    nodes = [g for g in root.iter(S.q("g")) if "node" in S.classes(g)]
    nodes[1].set("transform", nodes[0].get("transform"))
    return S.to_bytes(root)


def _swap_siblings(data):
    """Two leaf siblings ("make", "check") trade places: every node and edge is still there, the order is not."""
    root = S.tree(data)
    nodes = {next(g.iter(S.q("text"))).text: g for g in root.iter(S.q("g")) if "node" in S.classes(g)}
    a, b = nodes["make"], nodes["check"]
    ta, tb = a.get("transform"), b.get("transform")
    a.set("transform", tb)
    b.set("transform", ta)
    return S.to_bytes(root)


def _drop_arrows(data):
    root = S.tree(data)
    for g in root.iter(S.q("g")):
        for c in list(g):
            if "arrow" in S.classes(c):
                g.remove(c)
    return S.to_bytes(root)


@pytest.mark.parametrize("name,mutate,rule", [
    ("timeline", _move_event, "values_recovered"),
    ("timeline", _shift_dates, "values_recovered"),
    ("timeline", S.swap_series, "values_recovered"),
    ("timeline", S.add_script, "svg_allowed_elements"),
    ("dag", _reverse_edge, "edges_recovered"),
    ("dag", _drop_edge, "edges_recovered"),
    ("dag", _rename_node, "nodes_recovered"),
    ("dag", _drop_arrows, "arrows_present"),
    ("dag", S.drop_title, "title_and_description"),
    ("force", _stack_nodes, "nodes_apart"),
    ("tree", _reverse_edge, "edges_recovered"),
    ("tree", _swap_siblings, "outline_order"),
    ("flow", _drop_edge, "edges_recovered"),
    ("flow", S.add_event, "svg_no_event_attributes"),
])
def test_wrong_diagrams_are_caught(name, mutate, rule):
    s = case(name)
    good = S.made(s).bytes
    v = S.verdict(mutate(good), s)
    assert not v.ok and rule in S.failed(v), (rule, S.failed(v))
