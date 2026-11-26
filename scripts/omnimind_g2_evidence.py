"""Measure lane G2's forms (OMNIMIND, ADR-074) and write docs/quality/evidence/omnimind_g2.json.

Per form: whether it was made, the carried information the independent check measured (the share of source values
read back from the bytes), how many deliberately wrong artifacts the check caught out of how many, the bytes and the
time to make and to check. Also the agreement of the expression language's two implementations (Python, and the
runtime under node when node is here), and, when Playwright's Chromium is here, the browser run of the explorable
documents. Wrong artifacts are built here by editing bytes; nothing here is shared with the checks.

    PYTHONPATH=src python scripts/omnimind_g2_evidence.py [--out PATH] [--no-browser]
"""
from __future__ import annotations

import argparse
import json
import platform
import shutil
import struct
import subprocess
import sys
import time
import zlib
from pathlib import Path

import numpy as np

from neuroscifi.artifacts import check, expr, make
from neuroscifi.artifacts.check import _g2 as _checks  # noqa: F401
from neuroscifi.artifacts.check import browser
from neuroscifi.artifacts.make import _g2  # noqa: F401
from neuroscifi.artifacts.make import explorable
from neuroscifi.artifacts.spec import Spec

ROOT = Path(__file__).resolve().parents[1]


def env(form, data, options=None, title="Evidence sample", description="Made to measure lane G2."):
    return {"spec": form, "version": 1, "title": title, "description": description, "data": data,
            "options": options or {}, "sources": ["request"], "made_by": {"generator": "", "version": 0}}


# ---------------------------------------------------------------------------------------------------------------
# Wrong artifacts, by editing bytes


def _wav_edit(data: bytes, fn) -> bytes:
    at = data.index(b"data") + 8
    size = struct.unpack_from("<I", data, at - 4)[0]
    s = np.frombuffer(data, "<i2", size // 2, at).astype(np.float64)
    s = np.clip(np.round(fn(s.copy())), -32768, 32767).astype("<i2").tobytes()
    return data[:at] + s + data[at + size:]


def _abrupt(s):
    s[30000:30010] = 30000.0
    return s


def wav_wrongs(made):
    other = Spec.from_dict({**made.spec.to_dict(), "data": {**made.spec.data, "values": list(
        reversed(made.spec.data["values"]))}})
    return [("clipped", made.bytes, made.spec, lambda d: _wav_edit(d, lambda s: s * 3)),
            ("truncated data chunk", made.bytes, made.spec, lambda d: d[: len(d) // 2]),
            ("dc offset", made.bytes, made.spec, lambda d: _wav_edit(d, lambda s: s + 1500)),
            ("click", made.bytes, made.spec, lambda d: _wav_edit(d, _abrupt)),
            ("wrong pitches for the source", made.bytes, other, lambda d: d)]


def _stl_rec(data):
    n = struct.unpack_from("<I", data, 80)[0]
    return np.frombuffer(data, np.dtype([("n", "<f4", 3), ("v", "<f4", (3, 3)), ("a", "<u2")]), n, 84).copy()


def _stl(data, rec):
    return data[:80] + struct.pack("<I", len(rec)) + rec.tobytes()


def _invert(d):
    r = _stl_rec(d)
    r["v"][0] = r["v"][0][::-1].copy()
    return _stl(d, r)


def _scale(d):
    r = _stl_rec(d)
    r["v"] *= np.float32(1.1)
    return _stl(d, r)


def mesh_wrongs(made):
    other = Spec.from_dict({**made.spec.to_dict(), "options": {**made.spec.options, "unit": "m"}})
    return [("inverted face", made.bytes, made.spec, _invert),
            ("missing face", made.bytes, made.spec, lambda d: _stl(d, np.delete(_stl_rec(d), 3))),
            ("scaled copy", made.bytes, made.spec, _scale),
            ("wrong unit for the spec", made.bytes, other, lambda d: d),
            ("truncated", made.bytes, made.spec, lambda d: d[:-7])]


def _chunk(kind, body):
    return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))


def _png_pixels(data):
    from neuroscifi.artifacts.check import png
    return png.read(data).image.copy()


def _png_encode(pixels):
    h, w, c = pixels.shape
    raw = b"".join(b"\0" + pixels[y].astype(np.uint8).tobytes() for y in range(h))
    ihdr = struct.pack(">IIBBBBB", w, h, 8, {1: 0, 2: 4, 3: 2, 4: 6}[c], 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", ihdr) + _chunk(b"IDAT", zlib.compress(raw)) + _chunk(b"IEND", b"")


def _green(d):
    p = _png_pixels(d)
    p[0:8, 0:8, 0] = 0
    p[0:8, 0:8, 1] = 200
    p[0:8, 0:8, 2] = 0
    return _png_encode(p)


def _opaque_missing(d):
    p = _png_pixels(d)
    p[:, :, -1] = 255
    return _png_encode(p)


def _flip_byte(d):
    b = bytearray(d)
    b[40] ^= 0xFF
    return bytes(b)


def png_wrongs(made):
    grid = [list(r) for r in made.spec.data["grid"]]
    grid[0][0] = (grid[0][0] or 0) + 5
    other = Spec.from_dict({**made.spec.to_dict(), "data": {"grid": grid}})
    return [("a changed value", made.bytes, other, lambda d: d), ("bad crc", made.bytes, made.spec, _flip_byte),
            ("truncated", made.bytes, made.spec, lambda d: d[:-30]), ("green fill", made.bytes, made.spec, _green),
            ("missing value made opaque", made.bytes, made.spec, _opaque_missing),
            ("trailing bytes", made.bytes, made.spec, lambda d: d + b"junk")]


def apng_wrongs(made):
    slower = Spec.from_dict({**made.spec.to_dict(), "data": {**made.spec.data, "delays_ms": [200]}})
    more = Spec.from_dict({**made.spec.to_dict(), "data": {**made.spec.data,
                                                           "frames": made.spec.data["frames"] * 2}})
    return [("wrong delays", made.bytes, slower, lambda d: d), ("wrong frame count", made.bytes, more, lambda d: d),
            ("bad crc", made.bytes, made.spec, _flip_byte)]


def html_wrongs(made):
    def edit(old, new):
        return lambda d: d.decode().replace(old, new, 1).encode()
    return [("a second script", made.bytes, made.spec, edit('<script type="module">',
                                                            '<script>x()</script><script type="module">')),
            ("a changed runtime", made.bytes, made.spec, edit("const SVG =", "const SVG  =")),
            ("no policy", made.bytes, made.spec, edit('http-equiv="Content-Security-Policy"', 'name="generator"')),
            ("an image", made.bytes, made.spec, edit("<h1>", '<img src="x.png"><h1>')),
            ("an event attribute", made.bytes, made.spec, edit("<h1>", '<h1 onclick="x()">')),
            ("a comment", made.bytes, made.spec, edit("<h1>", "<!-- x --><h1>")),
            ("markup in the spec block", made.bytes, made.spec, edit('"version":1', '"version":1,"x":"</script>"')),
            ("a broken spec block", made.bytes, made.spec, edit('"version":1', '"version":1,')),
            ("a link", made.bytes, made.spec, edit("<h1>", '<link rel="stylesheet" href="https://example.org/x"><h1>'))]


# ---------------------------------------------------------------------------------------------------------------
# The forms measured


def samples():
    spring = env("explorable_model", {
        "parameters": [{"name": "m", "label": "Mass", "min": 0.1, "max": 5, "step": 0.1, "value": 1},
                       {"name": "k", "label": "Stiffness", "min": 1, "max": 50, "step": 1, "value": 10},
                       {"name": "c", "label": "Damping", "min": 0, "max": 5, "step": 0.1, "value": 0.4}],
        "quantities": [{"name": "omega", "label": "Natural frequency", "expr": "sqrt(k / m)"},
                       {"name": "energy", "label": "Energy", "expr": "0.5 * m * v^2 + 0.5 * k * x^2"}],
        "dynamics": {"state": [{"name": "v", "initial": "0", "update": "v + (-(k * x) - c * v) / m * dt"},
                               {"name": "x", "initial": "0.5", "update": "x + v * dt"}], "steps": 2000, "dt": 0.005},
        "views": [{"kind": "plot", "x": "time", "y": ["x"]}, {"kind": "table", "items": ["omega", "x", "energy"]},
                  {"kind": "plot", "x": "k", "y": ["omega"]}]})
    trace = env("explorable_steps", {"code": ["look", "found"], "arrays": [{"name": "a"}], "steps": [
        {"note": "Look at the middle.", "line": 0, "arrays": [[1, 3, 5, 7, 9]], "marks": [{"array": 0, "index": 2}]},
        {"note": "Found.", "line": 1, "arrays": [[1, 3, 5, 7, 9]], "marks": [{"array": 0, "index": 2,
                                                                               "kind": "found"}]}]})
    revolve = env("solid_revolve", {"profile": [[0, 0], [3, 0], [3.4, 0.6], [4.2, 3], [3.2, 6.5], [2.1, 8],
                                                [2.6, 9.2], [2.4, 9.4], [1.6, 8.2], [0, 8.2]]}, {"segments": 48})
    rng = np.random.default_rng(1)
    grid = rng.normal(20, 5, (24, 32)).round(2).tolist()
    grid[3][4] = None
    return [
        ("sound_tone", env("sound_tone", {"frequency_hz": 440, "duration_s": 1.0}), None),
        ("sound_sequence", env("sound_sequence", {"notes": [{"frequency_hz": f, "duration_s": 0.25}
                                                            for f in (262, 294, 330, 349, 392)]}), None),
        ("sound_sonify", env("sound_sonify", {"values": [float(v) for v in rng.normal(50, 15, 60).round(1)]}),
         wav_wrongs),
        ("solid_box", env("solid_box", {"size": [2, 3, 4]}), mesh_wrongs),
        ("solid_extrude", env("solid_extrude", {"outline": [[0, 0], [10, 0], [10, 6], [0, 6]], "height": 2,
                                                "holes": [[[2, 2], [4, 2], [4, 4], [2, 4]]]}), mesh_wrongs),
        ("solid_revolve", revolve, mesh_wrongs),
        ("surface_grid", env("surface_grid", {"values": rng.normal(0, 1, (20, 30)).round(3).tolist(), "x": [0, 3],
                                              "y": [0, 2], "base_z": -5}), mesh_wrongs),
        ("solid_points", env("solid_points", {"points": rng.uniform(-5, 5, (50, 3)).round(3).tolist()}),
         mesh_wrongs),
        ("image_raster", env("image_raster", {"grid": grid}, {"warn_above": 28}), png_wrongs),
        ("image_apng", env("image_apng", {"frames": [[[float((i + j + k) % 7) for j in range(16)] for i in range(12)]
                                                     for k in range(12)], "delays_ms": [80]}), apng_wrongs),
        ("explorable_model", spring, html_wrongs),
        ("explorable_steps", trace, html_wrongs),
        ("solid_view", explorable.solid_view_spec(revolve), html_wrongs),
    ]


def measure_form(value, wrongs):
    start = time.perf_counter()
    made = make.make(value)
    made_s = time.perf_counter() - start
    start = time.perf_counter()
    verdict = check.check(made.bytes, made.media_type, made.spec)
    check_s = time.perf_counter() - start
    carried = [f.measure for f in verdict.technical if f.rule == "values_recovered"]
    row = {"made": True, "media_type": made.media_type, "bytes": len(made.bytes), "check_ok": verdict.ok,
           "carried_information": carried[0] if carried else None, "make_s": round(made_s, 4),
           "check_s": round(check_s, 4), "deterministic": made.bytes == make.make(value).bytes}
    geometry = [f for f in verdict.technical if f.rule in ("bounds", "volume", "triangle_count", "watertight")]
    if geometry:      # a solid's carried information: its size, its analytic volume and its triangles, read back
        row["geometry_matches"] = all(f.passed for f in geometry)
    if wrongs:
        caught = []
        for name, data, against, edit in wrongs(made):
            caught.append((name, not check.check(edit(data), made.media_type, against).ok))
        row["wrong_caught"] = sum(c for _, c in caught)
        row["wrong_total"] = len(caught)
        row["wrong_missed"] = [n for n, c in caught if not c]
    return row, made


def agreement():
    node = shutil.which("node")
    if node is None:
        return {"run": False, "reason": "node is not installed here"}
    sys.path.insert(0, str(ROOT / "tests" / "artifacts"))
    import test_g2_agreement as t
    cases = t._cases()
    done = subprocess.run([node, "--input-type=module", "-e", t.DRIVER % json.dumps(t.RUNTIME.as_uri())],
                          input=json.dumps(cases), capture_output=True, text=True, timeout=120, check=True)
    js = json.loads(done.stdout)
    both_ok = both_refused = values = same = 0
    for case, j in zip(cases, js, strict=True):
        try:
            tree = expr.parse(case["src"])
        except expr.ExprError:
            both_refused += not j["ok"]
            continue
        if not j["ok"] or t._floats(tree) != t._floats(j["tree"]):
            continue
        both_ok += 1
        for e, got in zip(case["envs"], j["values"], strict=True):
            values += 1
            try:
                same += t._same(expr.evaluate(tree, e), got)
            except expr.ExprError:
                same += got == "error"
    return {"run": True, "expressions": len(cases), "accepted_by_both": both_ok, "refused_by_both": both_refused,
            "disagreeing_decisions_or_trees": len(cases) - both_ok - both_refused, "values_compared": values,
            "values_equal": same}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "docs" / "quality" / "evidence" / "omnimind_g2.json"))
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args(argv)
    forms = {}
    documents = {}
    for name, value, wrongs in samples():
        forms[name], made = measure_form(value, wrongs)
        if made.media_type.endswith("explorable+html"):
            documents[name] = made
    out = {"lane": "G2", "adr": "ADR-074", "what": "made artifacts measured by independent checks; not evidence of "
           "any claim (made artifacts never are)", "host": {"machine": platform.machine(),
                                                            "python": platform.python_version()},
           "forms": forms, "expression_agreement": agreement()}
    ok, reason = browser.available()
    if args.no_browser or not ok:
        out["browser_run"] = {"run": False, "reason": "skipped by request" if args.no_browser else reason}
    else:
        runs = {}
        for name, made in documents.items():
            for scheme in ("light", "dark"):
                start = time.perf_counter()
                v = browser.run(made.bytes, made.spec, color_scheme=scheme)
                moved = [f.measure for f in v.technical if f.rule == "values_follow_parameters"]
                runs[f"{name}/{scheme}"] = {"ok": v.ok, "seconds": round(time.perf_counter() - start, 2),
                                            "failed": [f.rule for f in v.technical if not f.passed],
                                            "values_follow_parameters": moved[0] if moved else None}
        out["browser_run"] = {"run": True, "axe": "not run here (the e2e test runs it where axe-core is installed)",
                              "documents": runs}
    text = json.dumps(out, indent=1, sort_keys=True) + "\n"
    Path(args.out).write_text(text)
    print(f"wrote {args.out} ({len(text)} bytes)")
    failures = [n for n, r in forms.items() if not r["check_ok"] or r.get("wrong_missed") or not r["deterministic"]]
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
