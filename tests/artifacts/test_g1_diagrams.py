"""Lane G1: timeline, node-link graph (layered and force), tree and flow of steps; recovered and wrong ones caught."""
import copy
import datetime
import re

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

CASES = {"timeline": ("timeline", TIMELINE), "dag": ("graph_nodelink", DAG), "force": ("graph_nodelink", CYCLIC),
         "tree": ("graph_tree", TREE), "flow": ("flow_steps", FLOW)}


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
    assert keys == ["", "recall", "store"]                  # edge kinds by dash, in a legend


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


def test_bad_structures_are_refused():
    t = copy.deepcopy(TREE)
    t["nodes"][1]["parent"] = "make"                         # a cycle
    t["nodes"][0]["parent"] = None
    with pytest.raises(ValueError):
        S.made(S.spec("graph_tree", "Cycle", t))
    t2 = copy.deepcopy(TREE)
    t2["nodes"][1]["parent"] = None                          # two roots
    with pytest.raises(ValueError, match="one root"):
        S.made(S.spec("graph_tree", "Two roots", t2))
    g = copy.deepcopy(DAG)
    g["edges"].append({"from": "q", "to": "nowhere"})
    with pytest.raises(ValueError):
        S.made(S.spec("graph_nodelink", "Dangling", g))
    tl = copy.deepcopy(TIMELINE)
    tl["spans"][0]["end"] = tl["spans"][0]["start"] - 1
    with pytest.raises(ValueError, match="ends before"):
        S.made(S.spec("timeline", "Backwards", tl))


def test_long_node_labels_are_cut_with_the_full_label_kept():
    g = copy.deepcopy(DAG)
    g["nodes"][0]["label"] = "A question that is far too long to fit inside one node box"
    s = S.spec("graph_nodelink", "Long", g)
    m = S.made(s)
    assert "…" in m.bytes.decode()
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
    edges.remove(list(edges)[0])
    return S.to_bytes(root)


def _rename_node(data):
    root = S.tree(data)
    node = next(g for g in root.iter(S.q("g")) if "node" in S.classes(g))
    next(node.iter(S.q("text"))).text = "Somebody else"
    return S.to_bytes(root)


def _stack_nodes(data):
    root = S.tree(data)
    nodes = [g for g in root.iter(S.q("g")) if "node" in S.classes(g)]
    nodes[1].set("transform", "translate(0,0)")
    a = next(iter(nodes[0]))
    b = next(iter(nodes[1]))
    for k in ("x", "y"):
        if a.get(k) is not None:
            b.set(k, a.get(k))
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
    ("flow", _drop_edge, "edges_recovered"),
    ("flow", S.add_event, "svg_no_event_attributes"),
])
def test_wrong_diagrams_are_caught(name, mutate, rule):
    s = case(name)
    good = S.made(s).bytes
    v = S.verdict(mutate(good), s)
    assert not v.ok and rule in S.failed(v), (rule, S.failed(v))
