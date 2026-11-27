"""One fixed specification per G1 form, for the determinism test (golden digests) and the evidence script."""
import math

T0 = 1767225600


def _spec(form, title, data, options=None):
    return {"spec": form, "version": 1, "title": title, "description": "", "data": data, "options": options or {},
            "sources": ["request"], "made_by": {"generator": "", "version": 0}}


XY = {"x": {"label": "Week"}, "y": {"label": "Rate", "unit": "per minute"},
      "series": [{"name": f"Runner {c}", "points": [[w, round(60 - 0.5 * w * (k + 1) + math.sin(w + k), 6)]
                                                    for w in range(1, 25)]} for k, c in enumerate("ABC")]}
BAR = {"categories": ["Jan", "Feb", "Mar", "Apr"], "x": {"label": "Month"}, "y": {"label": "Rain", "unit": "mm"},
       "series": [{"name": "Lisbon", "values": [99, 88, 61, 64]}, {"name": "Oslo", "values": [49, 36, 47, None]}]}
TABLE = {"columns": [{"name": "City", "kind": "text", "values": ["Lisbon", "=Oslo", ""]},
                     {"name": "Rain", "kind": "number", "unit": "mm", "values": [99.5, 0.1 + 0.2, None]},
                     {"name": "When", "kind": "time", "values": [T0, T0 + 0.25, None]}]}
NODES = [{"id": f"n{i}", "label": f"Node {i}"} for i in range(9)]

SPECS = {
    "chart.line": _spec("chart.line", "Rates", XY),
    "chart.scatter": _spec("chart.scatter", "Rates", XY),
    "chart.bar": _spec("chart.bar", "Rain", BAR, {"mode": "grouped"}),
    "chart.histogram": _spec("chart.histogram", "Spread", {"x": {"label": "Value"},
                                                           "values": [round(math.sin(i) * 50 + i % 7, 3)
                                                                      for i in range(300)]}),
    "chart.heatmap": _spec("chart.heatmap", "Grid", {"x": {"label": "Hour", "categories": ["08", "09", "10"]},
                                                     "y": {"label": "Day", "categories": ["Mon", "Tue"]},
                                                     "z": {"label": "Load"}, "values": [[1, 2, 3], [4, None, 6]]}),
    "chart.small_multiples": _spec("chart.small_multiples", "Rates", XY),
    "timeline": _spec("timeline", "Plan", {"x": {"label": "Date", "scale": "time"},
                                           "events": [{"lane": "A", "at": T0 + 86400 * k, "label": f"E{k}"}
                                                      for k in range(0, 40, 7)],
                                           "spans": [{"lane": "B", "start": T0, "end": T0 + 86400 * 20,
                                                      "label": "Build"}]}),
    "graph.nodelink": _spec("graph.nodelink", "Ring", {"directed": False, "nodes": NODES,
                                                       "edges": [{"from": f"n{i}", "to": f"n{(i * 4 + 1) % 9}"}
                                                                 for i in range(9)]}),
    "graph.tree": _spec("graph.tree", "Tree", {"nodes": [{"id": "r", "label": "root", "parent": None}]
                                               + [{"id": f"c{i}", "label": f"child {i}",
                                                   "parent": "r" if i < 3 else f"c{i % 3}"} for i in range(8)]}),
    "flow.steps": _spec("flow.steps", "Flow", {"steps": [{"id": "a", "label": "Start", "kind": "start"},
                                                         {"id": "b", "label": "Ok?", "kind": "decision"},
                                                         {"id": "c", "label": "Fix"}, {"id": "d", "label": "End",
                                                                                         "kind": "end"}],
                                               "links": [{"from": "a", "to": "b"}, {"from": "b", "to": "d",
                                                                                    "label": "yes"},
                                                         {"from": "b", "to": "c", "label": "no"},
                                                         {"from": "c", "to": "b"}]}),
    "table.html": _spec("table.html", "Rain", TABLE),
    "document.html": _spec("document.html", "Report", {"blocks": [
        {"kind": "heading", "level": 1, "text": "Findings"},
        {"kind": "paragraph", "parts": [{"text": "See "}, {"ref": "f"}, {"text": "."}]},
        {"kind": "figure", "id": "f", "caption": "Rain", "figure": _spec("chart.bar", "Rain", BAR)},
        {"kind": "table", "id": "t", "caption": "Rain", "table": TABLE}]}),
    "document.md": _spec("document.md", "Report", {"blocks": [
        {"kind": "heading", "level": 1, "text": "Findings"},
        {"kind": "paragraph", "parts": [{"text": "See "}, {"ref": "t"}, {"text": "."}]},
        {"kind": "table", "id": "t", "caption": "Rain", "table": TABLE}]}),
    "data.csv": _spec("data.csv", "Rain", TABLE),
    "data.json": _spec("data.json", "Rain", TABLE),
    "animation.svg": _spec("animation.svg", "Rain by year", {"form": "chart.bar", "frames": [
        {"label": str(2020 + k), "data": {**BAR, "series": [{"name": "Lisbon", "values": [99 - k, 88, 61, 64]},
                                                             {"name": "Oslo", "values": [49, 36 + k, 47, None]}]}}
        for k in range(3)]}),
}
