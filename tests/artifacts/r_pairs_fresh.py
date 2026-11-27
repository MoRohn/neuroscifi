"""Lane R's FRESH pairs: written after the mechanism was frozen (commit 677cf22) and measured once, without any
change to relate.py afterwards (docs/quality/evidence/omnimind_r.json, "fresh"). Same author as the mechanism, so
not independent: a held-out look of my own, weaker than the benchmark's. Built by hand from the formats."""
from __future__ import annotations

import io
import json
import math
import struct
import wave

from r_pairs import csv, midi


def wav_rate(rate: int, seconds: float = 0.5) -> bytes:
    n = int(rate * seconds)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"".join(struct.pack("<h", int(6000 * math.sin(2 * math.pi * 300 * i / rate)))
                               for i in range(n)))
    return buf.getvalue()


def stl_box(w, d, h) -> bytes:
    v = [(x, y, z) for z in (0, h) for y in (0, d) for x in (0, w)]
    f = [(0, 2, 1), (1, 2, 3), (4, 5, 7), (4, 7, 6), (0, 1, 5), (0, 5, 4), (2, 6, 7), (2, 7, 3), (0, 4, 6),
         (0, 6, 2), (1, 3, 7), (1, 7, 5)]
    out = b"\x00" * 80 + struct.pack("<I", len(f))
    for a, b, c in f:
        out += struct.pack("<3f", 0, 0, 0) + b"".join(struct.pack("<3f", *v[i]) for i in (a, b, c)) + b"\x00\x00"
    return out


def geojson(n: int) -> bytes:
    feats = [{"type": "Feature", "properties": {"name": f"s{i}"},
              "geometry": {"type": "Point", "coordinates": [-1.0 + i * 0.1, 52.0]}} for i in range(n)]
    return json.dumps({"type": "FeatureCollection", "features": feats}).encode()


def svg_plain(w, h) -> bytes:
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}">'
            f'<rect x="10" y="10" width="50" height="40"/></svg>').encode()


def made_line(rows):
    from neuroscifi.artifacts import make
    spec = {"spec": "chart.line", "version": 1, "title": "Sales", "description": "", "options": {},
            "sources": ["request"], "made_by": {"generator": "", "version": 0},
            "data": {"x": {"label": "Year"}, "y": {"label": "Sales"},
                     "series": [{"name": "Sales", "points": [[float(x), float(y)] for x, y in rows]}]}}
    return make.make(spec).bytes


LOWS = [("Mon", 1.5), ("Tue", -3.5), ("Wed", 0.0), ("Thu", 2.25)]
CALLS = [("c1", 3), ("c2", 12), ("c3", 7.5)]
SALES = [(2019, 40.0), (2020, 55.5), (2021, 61.0), (2022, 48.0)]
DEPLOY = ["build started", "tests passed", "image pushed", "rollout finished"]


def _logs(times_b):
    a = "".join(f"2026-07-0{1}T10:{10 + 5 * i:02d}:00Z ci: {e}\n" for i, e in enumerate(DEPLOY)) + \
        "2026-07-01T10:12:00Z ci: cache warm\n"
    b = "".join(f"Jul  1 12:{m:02d}:00 host deploy[7]: {e}\n" for m, e in sorted(zip(times_b, DEPLOY)))
    return a.encode(), ("Jul  1 12:00:30 host deploy[7]: heartbeat\n" + b).encode()


def pairs():
    out = []
    lows2 = list(LOWS)
    lows2[2] = ("Wed", -6.0)
    out.append(("doc_min_csv", "Was the coldest night as cold as the note says?",
                {"note.txt": "Note.\nThe coldest night was -3.5 °C.\n".encode(),
                 "nights.csv": csv(["night", "low (°C)"], LOWS)},
                {"note.txt": "Note.\nThe coldest night was -3.5 °C.\n".encode(),
                 "nights.csv": csv(["night", "low (°C)"], lows2)}))
    la, lb = _logs([10, 15, 20, 25])
    _, lb2 = _logs([10, 15, 27, 25])
    out.append(("two_logs_order", "Do the two logs show the deployment steps in the same order?",
                {"ci.log": la, "deploy.log": lb}, {"ci.log": la, "deploy.log": lb2}))
    out.append(("doc_rate_wav", "Is the audio sampled at the rate the README gives?",
                {"README.md": b"# Clip\n\nThe audio is sampled at 16 kHz.\n", "clip.wav": wav_rate(16000)},
                {"README.md": b"# Clip\n\nThe audio is sampled at 16 kHz.\n", "clip.wav": wav_rate(8000)}))
    sales2 = list(SALES)
    sales2[1] = (2020, 70.0)
    out.append(("made_chart_csv", "Was this chart drawn from this table?",
                {"sales.svg": made_line(SALES), "sales.csv": csv(["Year", "Sales"], SALES)},
                {"sales.svg": made_line(SALES), "sales.csv": csv(["Year", "Sales"], sales2)}))
    out.append(("doc_count_geo", "Does the map have as many points as the text says?",
                {"text.md": b"# Stations\n\nThe map marks 4 points.\n", "stations.geojson": geojson(4)},
                {"text.md": b"# Stations\n\nThe map marks 4 points.\n", "stations.geojson": geojson(5)}))
    resp = [("r1", 200), ("r2", 260), ("r3", 230)]
    resp2 = [("r1", 200), ("r2", 290), ("r3", 230)]
    out.append(("doc_mean_ms", "Is the mean response time in the summary right?",
                {"summary.md": b"# Load test\n\nMean response time: 230 ms.\n",
                 "requests.csv": csv(["request", "response_ms"], resp)},
                {"summary.md": b"# Load test\n\nMean response time: 230 ms.\n",
                 "requests.csv": csv(["request", "response_ms"], resp2)}))
    calls2 = [("c1", 3), ("c2", 14), ("c3", 7.5)]
    out.append(("doc_longest_csv", "Did the longest call last as long as the log book says?",
                {"book.txt": b"Log book.\nThe longest call lasted 12 minutes.\n",
                 "calls.csv": csv(["call", "Duration (min)"], CALLS)},
                {"book.txt": b"Log book.\nThe longest call lasted 12 minutes.\n",
                 "calls.csv": csv(["call", "Duration (min)"], calls2)}))
    out.append(("doc_notes_midi", "Does the MIDI file have the number of notes the description gives?",
                {"about.txt": b"About.\nThe tune has 6 notes.\n", "tune.mid": midi([60, 62, 64, 65, 67, 69])},
                {"about.txt": b"About.\nThe tune has 6 notes.\n", "tune.mid": midi([60, 62, 64, 65, 67, 69, 71])}))
    out.append(("doc_size_svg", "Is the drawing the size the note says?",
                {"note.txt": b"Note.\nThe drawing is 400 by 300.\n", "d.svg": svg_plain(400, 300)},
                {"note.txt": b"Note.\nThe drawing is 400 by 300.\n", "d.svg": svg_plain(400, 320)}))
    out.append(("readme_box_stl", "Does the STL have the size in the README?",
                {"README.md": b"# Block\n\nOverall size: 40 x 20 x 10 mm.\n", "block.stl": stl_box(40, 20, 10)},
                {"README.md": b"# Block\n\nOverall size: 40 x 20 x 10 mm.\n", "block.stl": stl_box(40, 20, 12)}))
    recs = [{"item": "a", "amount": 150}, {"item": "b", "amount": 200}, {"item": "c", "amount": 100}]
    recs2 = [dict(r) for r in recs]
    recs2[0]["amount"] = 175
    out.append(("doc_sum_json", "Does the total in the memo match the JSON records?",
                {"memo.txt": b"Memo.\nTotal: 450.\n", "items.json": json.dumps(recs).encode()},
                {"memo.txt": b"Memo.\nTotal: 450.\n", "items.json": json.dumps(recs2).encode()}))
    rows = [(i, i * 2) for i in range(7)]
    out.append(("csv_csv_rows", "Do both files have the same number of rows?",
                {"a.csv": csv(["k", "v"], rows), "b.csv": csv(["k", "w"], rows)},
                {"a.csv": csv(["k", "v"], rows), "b.csv": csv(["k", "w"], rows + [(7, 14)])}))
    return out


def unrelated():
    return [
        ("Does the letter match the recording?",
         {"letter.txt": b"Dear Ann,\nShe turned 30 in May and we had cake.\n", "r.wav": wav_rate(8000)}),
        ("Do they agree?", {"a.csv": csv(["name", "age"], [("x", 3)]), "b.csv": csv(["city", "km"], [("y", 9)])}),
        ("Is the code consistent with the data?",
         {"m.py": b"def f(x):\n    return x * 2\n", "d.csv": csv(["k", "v"], [(1, 2), (2, 4)])}),
        ("Does the drawing show the readings?",
         {"d.svg": svg_plain(100, 80), "r.csv": csv(["t", "reading"], [(1, 2.5), (2, 3.5)])}),
    ]
