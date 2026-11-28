"""Lane P's evidence (ADR-076): docs/quality/evidence/omnimind_p.json.

    omnimind_p_evidence.py [--before SUMMARY.json] [--out FILE] [--check]

- benchmark: the development split's `made` and `transform` families through the artifacts subject, now (and the
  summary of a run before lane P, when --before names one written by `bench_omni.py run`). Development only.
- coverage: for each representation kind of a small corpus built here by hand x each builder: "builds" (and whether
  the made artifact passed its independent check) or "cannot" with the builder's reason.
- foresight: every prediction of `forms.foresee` paired with what the made bytes and the check showed, counted by
  outcome (right, under-/over-predicted, false alarm, missed), over the corpus productions and the development
  transform tasks.
- cost: seconds and bytes per production of the corpus (wall clock on this host, one run each).
Nothing is stored (every production runs without an individual). The file holds summaries only; its size is printed.
"""
from __future__ import annotations

import argparse
import datetime
import io
import json
import math
import struct
import sys
import time
import wave
import zipfile
import zlib
from collections import Counter
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from neuroscifi.artifacts import forms  # noqa: E402
from neuroscifi.artifacts import produce as P  # noqa: E402
from neuroscifi.artifacts import produce_builders as B  # noqa: E402
from neuroscifi.artifacts.types import Limits  # noqa: E402

OUT = ROOT / "docs/quality/evidence/omnimind_p.json"


def _csv_temps() -> bytes:
    d0 = datetime.date(2025, 1, 1)
    rows = ["date,temperature_c,rain_mm"] + [f"{d0 + datetime.timedelta(days=i)},{10 + 8 * math.sin(i / 58):.2f},"
                                             f"{(i * 7) % 11 / 2:.1f}" for i in range(365)]
    return ("\n".join(rows) + "\n").encode()


def _wav() -> bytes:
    rate = 8000
    x = np.concatenate([0.5 * np.sin(2 * np.pi * f * np.arange(rate // 2) / rate) for f in (440.0, 660.0, 330.0)])
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes((x * 32767).astype("<i2").tobytes())
    return buf.getvalue()


def _png() -> bytes:
    px = (np.add.outer(np.arange(32), np.arange(48)) * 3 % 256).astype(np.uint8)
    raw = b"".join(b"\x00" + bytes(row) for row in px)

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 48, 32, 8, 0, 0, 0, 0)) +
            chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def _zip(files) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, text in files.items():
            z.writestr(zipfile.ZipInfo(name, (2020, 1, 1, 0, 0, 0)), text)
    return buf.getvalue()


MODEL = {"format": "neuroscifi.model/1", "title": "Falling ball",
         "parameters": [{"name": "g", "label": "Gravity", "min": 1, "max": 20, "step": 0.1, "value": 9.8},
                        {"name": "h0", "label": "Height", "min": 1, "max": 100, "step": 1, "value": 50}],
         "quantities": [{"name": "t_fall", "label": "Time to fall", "expr": "sqrt(2*h0/g)", "unit": "s"}],
         "views": [{"kind": "table", "items": ["t_fall"]}]}

CORPUS = {
    # representation kind -> (file name, bytes, request)
    "table (time series)": ("temps.csv", _csv_temps(), "how did temperature_c change over the year?"),
    "table (categories)": ("cities.csv", b"city,people,area_km2\nOslo,709,454\nBergen,286,465\nTromso,77,2521\n"
                                         b"Bodo,53,1395\nAlta,21,3849\nMolde,32,363\n", "compare the cities"),
    "signal": ("tones.wav", _wav(), "what is in this sound?"),
    "raster": ("ramp.png", _png(), "show me this picture"),
    "mesh": ("cube.obj", b"v 0 0 0\nv 1 0 0\nv 0 1 0\nv 1 1 0\nv 0 0 1\nv 1 0 1\nv 0 1 1\nv 1 1 1\n"
                         b"f 1 3 4 2\nf 5 6 8 7\nf 1 2 6 5\nf 3 7 8 4\nf 1 5 7 3\nf 2 4 8 6\n", "show me this shape"),
    "features": ("places.geojson", json.dumps({"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"name": f"p{i}"}, "geometry": {"type": "Point",
                                                                          "coordinates": [10 + i * 0.3, 59 + i * 0.1]}}
        for i in range(12)]}).encode(), "where are these places?"),
    "tree": ("config.json", json.dumps({"server": {"port": 8080, "hosts": ["a", "b"]}, "debug": False,
                                        "paths": {"data": "/d", "logs": "/l"}}).encode(), "how is this organised?"),
    "graph (code)": ("pkg.zip", _zip({"pkg/__init__.py": "from .core import run\n",
                                      "pkg/core.py": "import json\nfrom .util import h\ndef run():\n    return h()\n",
                                      "pkg/util.py": "import os\ndef h():\n    return os.sep\n"}),
                     "how do these modules depend on each other?"),
    "document": ("plan.md", b"# Plan\n\nIntro.\n\n## Costs\n\nText.\n\n| item | eur |\n|---|---|\n| a | 3 |\n| b | 5 |\n\n"
                            b"## Risks\n\nMore.\n\n### Weather\n\nRain.\n", "what is in this plan?"),
    "bundle": ("mixed.zip", _zip({"a/one.txt": "one\n", "b/two.csv": "x\n1\n", "c.json": '{"k": 1}'}),
               "what is in this archive?"),
    "model file": ("ball.json", json.dumps(MODEL).encode(), "what happens when I change gravity?"),
}


def coverage() -> tuple[dict, list]:
    table, productions = {}, []
    for kind, (name, data, request) in CORPUS.items():
        pieces, notes = P.interpret(None, [P.Given(name, data)], Limits())
        row = {}
        for form in B.BUILDERS:
            try:
                built = B.build(form, pieces, request)
            except B.CannotBuild as why:
                row[form] = "cannot: " + str(why)[:160]
                continue
            # made, checked and repaired as the pipeline does (at most three rounds)
            run = P._Run(request, forms.Context(), Limits(), None)
            result = P.make_checked(built.spec, Limits(), run)
            repaired = sum(1 for r in result["rounds"] if r.get("repair"))
            if result["made"] is None:
                row[form] = "builds; not made: " + str((result["refusal"] or {}).get("message", ""))[:140]
            else:
                failed = [f.rule for f in result["verdict"].technical if not f.passed]
                row[form] = ("builds; check " + ("passed" if result["verdict"].ok else
                                                 "FAILED (" + ", ".join(failed[:3]) + ")") +
                             (f" after {repaired} repair(s)" if repaired else ""))
        table[kind] = row
        start = time.perf_counter()
        result = P.produce(None, request, [P.Given(name, data)])
        productions.append({"source": kind, "request": request, "status": result.status,
                            "form": (result.record.get("made") or {}).get("form"),
                            "seconds": round(time.perf_counter() - start, 4),
                            "bytes": len(result.made.bytes) if result.made else 0,
                            "check_ok": (result.record.get("check") or {}).get("ok"),
                            "carried": (result.record.get("check") or {}).get("carried"),
                            "foresight": [{k: p[k] for k in ("what", "outcome")} for p in
                                          result.record.get("foresight", [])]})
    return table, productions


def development() -> tuple[dict, list]:
    from neuroscifi.acceptance.omni import made, subject, transform
    artifacts = subject.get("artifacts")
    out = {}
    for name, family in (("made", made), ("transform", transform)):
        s = family.score(artifacts, "development")
        out[name] = {k: s[k] for k in ("n", "right", "abstained", "wrong", "failed")}
    pairs = []
    for task in transform.tasks("development"):
        request = json.loads(task.files["request.json"])
        (name, data), = [(n, d) for n, d in task.files.items() if n != "request.json"]
        form = artifacts._TRANSFORM_FORMS[request["transform"]] or \
            {"line": "line_chart", "scatter": "scatter_chart"}[request["form"]]
        words = (f"a {request.get('form')} chart of {request.get('y')} against {request.get('x')}"
                 if request["transform"] == "csv_to_chart" else request["transform"].replace("_", " "))
        if request["transform"] == "table_to_sound":
            words = f"let me hear {request['column']}"
        result = P.produce(None, words, [P.Given(name, data)], form=form)
        pairs += [{k: p[k] for k in ("what", "outcome")} for p in result.record.get("foresight", [])]
    return out, pairs


def tally(pairs) -> dict:
    counts: dict[str, Counter] = {}
    for p in pairs:
        counts.setdefault(p["what"], Counter())[p["outcome"]] += 1
    return {what: dict(sorted(c.items())) for what, c in sorted(counts.items())}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--before", type=Path, help="a bench_omni.py run summary written before lane P")
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args(argv)
    table, productions = coverage()
    dev, dev_pairs = development()
    before = None
    if args.before:
        b = json.loads(args.before.read_text())
        fams = b["splits"]["development"]["families"]
        before = {"commit": b.get("commit") or "c1f5f2f (git archive)",
                  **{f: {k: fams[f][k] for k in ("n", "right", "abstained", "wrong", "failed")}
                     for f in ("made", "transform") if f in fams}}
    corpus_pairs = [p for prod in productions for p in prod["foresight"]]
    value = {
        "label": "OMNIMIND lane P evidence: made by measurement on this host; development split only; nothing stored",
        "benchmark_development": {"before": before, "after": dev},
        "builders_coverage": table,
        "productions": productions,
        "foresight": {"corpus": tally(corpus_pairs), "development_transform": tally(dev_pairs),
                      "rule": "numbers right within a factor of two (under-predicted: observed more than twice the "
                              "prediction); problems right, false alarm (predicted, not found) or missed (found, not "
                              "predicted); a problem no check here observes is 'not observable here'"},
        "cost": {"seconds_max": max(p["seconds"] for p in productions),
                 "seconds_median": float(np.median([p["seconds"] for p in productions])),
                 "bytes_max": max(p["bytes"] for p in productions)},
    }
    text = json.dumps(value, indent=1, sort_keys=True, ensure_ascii=False) + "\n"
    args.out.write_text(text)
    print(f"wrote {args.out} ({len(text.encode())} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
