"""OMNIMIND lane G1 evidence: per form, specs made, carried information measured by the independent checkers,
deliberately wrong artifacts caught of how many, bytes and time. Writes docs/quality/evidence/omnimind_g1.json
(a summary, not a history) unless --out is given; --print shows it.

The specs are generated here from a fixed seed (five per form, varied sizes); the wrong artifacts are made by
editing the made bytes with mutations written in this script (not by the generators, and not read by the
checkers). Caveat stated in the file: the mutations and the checkers are written by the same lane, so "caught"
measures the checkers against the faults this lane thought of; the OMNIMIND benchmark (lane Z) is the
independent measure.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import statistics
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

from neuroscifi.artifacts import check, make
from neuroscifi.artifacts.check import _g1 as _check_g1  # noqa: F401
from neuroscifi.artifacts.make import _g1 as _make_g1  # noqa: F401

SVG = "http://www.w3.org/2000/svg"
ET.register_namespace("", SVG)
OUT = Path(__file__).resolve().parents[1] / "docs" / "quality" / "evidence" / "omnimind_g1.json"
T0 = 1767225600
PER_FORM = 5


def spec(form, title, data, options=None):
    return {"spec": form, "version": 1, "title": title, "description": "", "data": data, "options": options or {},
            "sources": ["request"], "made_by": {"generator": "", "version": 0}}


def xy(r, k):
    n_series, n = 1 + k % 4, 12 + 40 * k
    return {"x": {"label": "Step"}, "y": {"label": "Value", "unit": "units"},
            "series": [{"name": f"Series {chr(65 + s)}",
                        "points": [[i, round(r.gauss(10 * (s + 1), 3) + i * 0.1, 4)] for i in range(n)]}
                       for s in range(n_series)]}


def bars(r, k):
    cats = [f"Group {i + 1}" for i in range(3 + 2 * k)]
    return {"categories": cats, "x": {"label": "Group"}, "y": {"label": "Count"},
            "series": [{"name": f"Year {2020 + s}", "values": [round(r.uniform(1, 90), 1) for _ in cats]}
                       for s in range(1 + k % 3)]}


def table(r, k):
    n = 3 + 20 * k
    return {"columns": [{"name": "Name", "kind": "text", "values": [f"Item {i}" if i % 7 else f"=item {i}"
                                                                    for i in range(n)]},
                        {"name": "Value", "kind": "number", "unit": "kg",
                         "values": [None if i % 11 == 5 else r.uniform(-1e3, 1e3) for i in range(n)]},
                        {"name": "Count", "kind": "integer", "values": [r.randint(-50, 50) for _ in range(n)]},
                        {"name": "Seen", "kind": "time", "values": [T0 + r.randint(0, 10 ** 7) + 0.5 * (i % 2)
                                                                    for i in range(n)]}]}


def nodes(r, k, directed):
    n = 5 + 4 * k
    ns = [{"id": f"n{i}", "label": f"Node {i}"} for i in range(n)]
    es = [{"from": f"n{i}", "to": f"n{j}"} for i in range(n) for j in range(i + 1, n) if r.random() < 2.0 / n]
    if not directed:
        es.append({"from": f"n{n - 1}", "to": "n0"})
    return {"directed": directed, "nodes": ns, "edges": es}


def specs_for(form, k):
    r = random.Random(f"{form}-{k}")
    if form in ("chart.line", "chart.scatter", "chart.small_multiples"):
        return spec(form, f"{form} {k}", xy(r, k))
    if form == "chart.bar":
        return spec(form, f"Bars {k}", bars(r, k), {"mode": "stacked" if k % 2 else "grouped"})
    if form == "chart.histogram":
        return spec(form, f"Spread {k}", {"x": {"label": "Value"},
                                          "values": [round(r.gauss(0, 1 + k), 3) for _ in range(50 + 300 * k)]})
    if form == "chart.heatmap":
        nx, ny = 3 + 2 * k, 2 + k
        return spec(form, f"Grid {k}", {"x": {"label": "Column", "categories": [f"C{i}" for i in range(nx)]},
                                        "y": {"label": "Row", "categories": [f"R{i}" for i in range(ny)]},
                                        "z": {"label": "Level"},
                                        "values": [[round(r.uniform(0, 100), 1) for _ in range(nx)] for _ in range(ny)]})
    if form == "timeline":
        lanes = [f"Lane {i}" for i in range(1 + k % 3)]
        return spec(form, f"Plan {k}", {"x": {"label": "Date", "scale": "time"},
                                        "events": [{"lane": lanes[i % len(lanes)], "at": T0 + 86400 * r.randint(0, 90),
                                                    "label": f"Event {i}"} for i in range(2 + 3 * k)],
                                        "spans": [{"lane": lanes[i % len(lanes)], "start": T0 + 86400 * 10 * i,
                                                   "end": T0 + 86400 * (10 * i + 15), "label": f"Span {i}"}
                                                  for i in range(1 + k)]})
    if form == "graph.nodelink":
        return spec(form, f"Graph {k}", nodes(r, k, directed=k % 2 == 0))
    if form == "graph.tree":
        n = 4 + 5 * k
        return spec(form, f"Tree {k}", {"nodes": [{"id": "n0", "label": "Root", "parent": None}] +
                                        [{"id": f"n{i}", "label": f"Node {i}", "parent": f"n{r.randint(0, i - 1)}"}
                                         for i in range(1, n)]})
    if form == "flow.steps":
        n = 3 + 2 * k
        steps = [{"id": f"s{i}", "label": f"Step {i}", "kind": "decision" if i % 3 == 2 else "step"}
                 for i in range(n)]
        links = [{"from": f"s{i}", "to": f"s{i + 1}"} for i in range(n - 1)] + \
                [{"from": f"s{i}", "to": f"s{max(0, i - 2)}", "label": "again"} for i in range(2, n, 3)]
        return spec(form, f"Flow {k}", {"steps": steps, "links": links})
    if form in ("table.html", "data.csv", "data.json"):
        return spec(form, f"Table {k}", table(r, k))
    if form in ("document.html", "document.md"):
        blocks = []
        for s in range(1 + k):
            blocks += [{"kind": "heading", "level": 1, "text": f"Section {s + 1}"},
                       {"kind": "paragraph", "text": f"Paragraph {s + 1}, written for the evidence run."}]
        blocks += [{"kind": "table", "id": "t", "caption": "Data", "table": table(r, 0)},
                   {"kind": "paragraph", "parts": [{"text": "See "}, {"ref": "t"}, {"text": "."}]}]
        if form == "document.html":
            blocks += [{"kind": "figure", "id": "f", "caption": "Bars", "figure": spec("chart.bar", "Bars", bars(r, 0))}]
        return spec(form, f"Report {k}", {"blocks": blocks})
    if form == "animation.svg":
        base = bars(r, 0)
        frames = [{"label": f"Frame {i}", "data": {**base, "series": [
            {"name": s["name"], "values": [round(v * (1 + 0.1 * i), 1) for v in s["values"]]} for s in base["series"]]}}
            for i in range(2 + k)]
        return spec(form, f"Animation {k}", {"form": "chart.bar", "frames": frames})
    raise KeyError(form)


# --- mutations: (name, function bytes -> bytes) per media type ----------------------------------------------

def _tree(data):
    return ET.fromstring(data)


def _bytes(root):
    return ET.tostring(root, encoding="utf-8")


def _cls(e):
    return set(e.get("class", "").split())


def m_scale(data):
    root = _tree(data)
    for g in root.iter(f"{{{SVG}}}g"):
        if {"axis"} <= _cls(g) and ({"x"} | {"y"}) & _cls(g):
            texts = [t for t in g.iter(f"{{{SVG}}}text") if t.text and re.fullmatch(r"-?\d+(\.\d+)?", t.text.strip())]
            if len(texts) >= 2:
                for t in texts:
                    t.text = repr(round(float(t.text) * 1.1, 6))
                return _bytes(root)
    return data


def m_swap(data):
    root = _tree(data)
    gs = [g for g in root.iter(f"{{{SVG}}}g") if "series" in _cls(g) and g.get("data-name")]
    names = [g.get("data-name") for g in gs]
    if len(set(names)) < 2:
        return data
    j = next(i for i, n in enumerate(names) if n != names[0])
    gs[0].set("data-name", names[j])
    gs[j].set("data-name", names[0])
    return _bytes(root)


def m_move(data):
    root = _tree(data)
    for e in root.iter():
        c = _cls(e)
        if "mark" in c and e.tag == f"{{{SVG}}}circle":
            e.set("cx", str(float(e.get("cx")) + 25))
            return _bytes(root)
        if "mark" in c and e.tag == f"{{{SVG}}}rect":
            e.set("y", str(float(e.get("y")) - 25))
            e.set("height", str(float(e.get("height")) + 25))
            return _bytes(root)
        if e.tag == f"{{{SVG}}}polyline" and "series" in c:
            pts = re.findall(r"-?\d+(?:\.\d+)?", e.get("points"))
            pts[1] = str(float(pts[1]) - 25)
            e.set("points", " ".join(f"{pts[i]},{pts[i + 1]}" for i in range(0, len(pts), 2)))
            return _bytes(root)
        if e.tag == f"{{{SVG}}}polyline" and "edge" in c:      # an edge detached from the node it entered
            pts = re.findall(r"-?\d+(?:\.\d+)?", e.get("points"))
            pts[-2] = str(float(pts[-2]) + 400)
            e.set("points", " ".join(f"{pts[i]},{pts[i + 1]}" for i in range(0, len(pts), 2)))
            return _bytes(root)
    return data


def m_rename(data):
    root = _tree(data)
    for g in root.iter(f"{{{SVG}}}g"):
        if "node" in _cls(g):
            t = next(g.iter(f"{{{SVG}}}text"))
            t.text = "Somebody else"
            return _bytes(root)
    return data


def m_script(data):
    return data.replace(b"</svg>", b"<script>alert(1)</script></svg>", 1)


def m_green(data):
    return re.sub(rb'(class="mark[^"]*"[^>]*?fill=")[^"]*"', rb'\1#2e8b57"', data, count=1) \
        if re.search(rb'class="mark', data) else data.replace(b'stroke="currentColor"', b'stroke="#2e8b57"', 1)


def m_title(data):
    return re.sub(rb"<title id=[^>]*>[^<]*</title>", b"", data, count=1)


def m_clip(data):
    return re.sub(rb'(class="title" x=")[^"]*"', rb'\g<1>99999"', data, count=1)


def m_event(data):
    return data.replace(b"<svg ", b'<svg onload="alert(1)" ', 1)


SVG_MUTATIONS = [("scale_off_10_percent", m_scale), ("swapped_series", m_swap), ("moved_mark", m_move),
                 ("renamed_node", m_rename), ("script_element", m_script), ("green_fill", m_green),
                 ("missing_title", m_title), ("clipped_label", m_clip), ("event_attribute", m_event)]
HTML_MUTATIONS = [("script_element", lambda d: d.replace(b"</table>", b"</table><script>x()</script>", 1)),
                  ("external_link", lambda d: re.sub(rb"<td([^>]*)>", rb'<td\1><a href="https://example.invalid">x</a>',
                                                     d, count=1)),
                  ("placeholder", lambda d: d.replace(b"<td", b"<td>TODO</td><td", 1) if b"<main>" in d else
                   re.sub(rb"(<td[^>]*>)-?\d", rb"\g<1>9", d, count=1)),
                  ("label_removed", lambda d: d.replace(b'class="made-by"', b'class="x"')),
                  ("changed_cell", lambda d: re.sub(rb"(<td class=\"num\">)(-?)(\d)", rb"\g<1>\g<2>9\3", d, count=1))]
MD_MUTATIONS = [("link", lambda d: d.replace(b"\n\n", b"\n\n[x](https://example.invalid)\n\n", 1)),
                ("raw_html", lambda d: d.replace(b"\n\n", b"\n\n<iframe></iframe>\n\n", 1)),
                ("placeholder", lambda d: d.replace(b"\n\n", b"\n\nTBD\n\n", 1)),
                ("label_removed", lambda d: d.replace(b"> Made by NeuroSciFi", b"> Written"))]
CSV_MUTATIONS = [("changed_value", lambda d: re.sub(rb"(\r\n[^,\r\n]*,-?)(\d)", rb"\g<1>9\2", d, count=1)),
                 ("lf_line_ends", lambda d: d.replace(b"\r\n", b"\n")),
                 ("formula_unguarded", lambda d: d.replace(b"'=", b"=", 1))]
JSON_MUTATIONS = [("changed_value", lambda d: re.sub(rb'("kind":"number","unit":"kg","values":\[)(-?)(\d)',
                                                      rb"\g<1>\g<2>9\3", d, count=1)),
                  ("nan", lambda d: d.replace(b"null", b"NaN", 1)),
                  ("label_removed", lambda d: d.replace(b"not evidence", b"evidence"))]
MUTATIONS = {"image/svg+xml": SVG_MUTATIONS, "text/html": HTML_MUTATIONS, "text/markdown": MD_MUTATIONS,
             "text/csv": CSV_MUTATIONS, "application/json": JSON_MUTATIONS}
CARRIED = ("values_recovered", "cells_match", "values_exact")
# Edits that change bytes but not what a reader sees: a heatmap's rows are read by their position on the y axis,
# so renaming the row groups is no fault.
NOT_FAULTS = {("chart.heatmap", "swapped_series")}


def measure(form):
    made_n = ok_n = 0
    shares, sizes, t_make, t_check = [], [], [], []
    caught: dict[str, list[int]] = {}
    digests = []
    for k in range(PER_FORM):
        s = specs_for(form, k)
        t = time.perf_counter()
        m = make.make(s)
        t_make.append(time.perf_counter() - t)
        made_n += 1
        sizes.append(len(m.bytes))
        digests.append(hashlib.sha256(m.bytes).hexdigest()[:12])
        t = time.perf_counter()
        v = check.check(m.bytes, m.media_type, s)
        t_check.append(time.perf_counter() - t)
        ok_n += v.ok
        share = next((f.measure for f in v.technical if f.rule in CARRIED and f.measure is not None), None)
        if share is not None:
            shares.append(share)
        for name, fn in MUTATIONS[m.media_type]:
            if (form, name) in NOT_FAULTS:
                continue
            bad = fn(m.bytes)
            if bad == m.bytes:
                continue                     # not applicable to this artifact
            row = caught.setdefault(name, [0, 0])
            row[1] += 1
            row[0] += not check.check(bad, m.media_type, s).ok
    return {"media_type": make.make(specs_for(form, 0)).media_type, "specs_made": made_n, "specs_passing": ok_n,
            "carried_information": ({"measured_on": len(shares), "mean": round(statistics.fmean(shares), 6),
                                     "min": round(min(shares), 6)} if shares else None),
            "wrong_artifacts": {"caught": sum(c for c, _n in caught.values()),
                                "of": sum(n for _c, n in caught.values()),
                                "by_fault": {k: f"{c}/{n}" for k, (c, n) in sorted(caught.items())}},
            "bytes": {"min": min(sizes), "median": int(statistics.median(sizes)), "max": max(sizes)},
            "seconds": {"make_median": round(statistics.median(t_make), 4), "make_max": round(max(t_make), 4),
                        "check_median": round(statistics.median(t_check), 4), "check_max": round(max(t_check), 4)},
            "digests": digests}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("--print", action="store_true")
    args = ap.parse_args(argv)
    forms = sorted(_make_g1.FORMS)
    result = {"lane": "G1", "what": "made artifacts per form, checked by independent readers",
              "host_note": "times are wall clock on the recording host; digests are of the made bytes",
              "caveat": ("specs, mutations and checkers are written by lane G1; 'caught' counts the faults this lane "
                         "thought of. The OMNIMIND benchmark (lane Z) is the independent measure."),
              "tolerance": "chart values within 0.5 % of the axis's tick span (heatmap 1 % of the ramp); data files "
                           "and table cells exact (or to stated decimals)",
              "first_run": ("the first run (same specs) had 79 of 80 specs passing and 465 of 490 wrong artifacts "
                            "caught: a histogram value on a shared bin edge was counted twice by the reader (fixed: "
                            "adjacent bins share one edge); document tables were not compared with their data "
                            "(fixed: every cell is); 'swapped_series' on a heatmap renames rows that are read by "
                            "position (no fault: excluded) and 'moved_mark' on a diagram nudged an edge end inside "
                            "its own node (no fault: now the edge is detached). The mutations were changed after "
                            "seeing that run."),
              "specs_per_form": PER_FORM, "python": sys.version.split()[0],
              "forms": {form: measure(form) for form in forms}}
    caught = sum(f["wrong_artifacts"]["caught"] for f in result["forms"].values())
    total = sum(f["wrong_artifacts"]["of"] for f in result["forms"].values())
    result["totals"] = {"specs_made": sum(f["specs_made"] for f in result["forms"].values()),
                        "specs_passing": sum(f["specs_passing"] for f in result["forms"].values()),
                        "wrong_artifacts_caught": caught, "wrong_artifacts": total}
    text = json.dumps(result, indent=1, sort_keys=False) + "\n"
    if args.print:
        print(text)
    args.out.write_text(text)
    print(f"wrote {args.out} ({len(text)} bytes): {result['totals']}")
    return 0 if caught == total and result["totals"]["specs_made"] == result["totals"]["specs_passing"] else 1


if __name__ == "__main__":
    sys.exit(main())
