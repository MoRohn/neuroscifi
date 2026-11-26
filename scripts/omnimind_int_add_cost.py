#!/usr/bin/env python3
"""The cost of `nsf artifact add` per family at the size limits (OMNIMIND integration lane, item 3).

    python scripts/omnimind_int_add_cost.py [--out docs/quality/evidence/omnimind_int.json] [--families a,b]

Each case builds one input here (near the 64 MiB input limit, or near the element limit where the decoder's
element bound comes first), creates a fresh individual in a temporary folder, and runs `artifacts.store.add` on it
in a FRESH Python process, so that the peak resident memory (VmHWM, Linux) is that one add's. Reported: input bytes,
wall seconds of the add (hash, sidecar write, decode, index, text layer, commit), peak RSS above the process's
baseline after imports, and the outcome (representation kind, partial, or the error's reason). One run per case on
a shared, loaded host: wall clock, not a benchmark of the decoders. The temporary folder is removed afterwards.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
MiB = 1 << 20

CHILD = r"""
import io, json, os, struct, sys, time, zipfile, zlib
import numpy as np
sys.path.insert(0, sys.argv[1])
from neuroscifi.artifacts import store as A
from neuroscifi.instance import store

def rss():
    for line in open("/proc/self/status"):
        if line.startswith("VmHWM:"):
            return int(line.split()[1]) * 1024
    return 0

def chunk(kind, body):
    return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))

def build(case):
    rng = np.random.default_rng(7)
    if case == "text":
        return "note.txt", (b"plain words of a long note\n" * (60 * 2**20 // 27))
    if case == "table":
        rows = [b"day,rain_mm,temp_c,site"] + [b"2024-01-%02d,%d.5,%d,s%d" % (1 + i % 28, i % 9, i % 30, i % 7)
                                               for i in range(1_200_000)]
        return "rain.csv", b"\n".join(rows) + b"\n"
    if case == "document":
        w = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
        paras = "".join(f"<w:p><w:r><w:t>Paragraph {i} of a long report.</w:t></w:r></w:p>" for i in range(60_000))
        doc = f'<?xml version="1.0"?><w:document {w}><w:body>{paras}</w:body></w:document>'.encode()
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("[Content_Types].xml", b"<Types/>")
            z.writestr("word/document.xml", doc)
        return "report.docx", out.getvalue()
    if case == "image":
        w, h = 4000, 4000
        raw = b"".join(b"\x00" + rng.integers(0, 256, w * 3, dtype=np.uint8).tobytes() for _ in range(h))
        return "big.png", (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
                           + chunk(b"IDAT", zlib.compress(raw, 1)) + chunk(b"IEND", b""))
    if case == "audio":
        samples = rng.integers(-20000, 20000, 15 * 2**20 * 2, dtype=np.int16).tobytes()   # 60 MiB, stereo
        fmt = struct.pack("<HHIIHH", 1, 2, 44100, 44100 * 4, 4, 16)
        body = b"WAVE" + b"fmt " + struct.pack("<I", 16) + fmt + b"data" + struct.pack("<I", len(samples)) + samples
        return "long.wav", b"RIFF" + struct.pack("<I", len(body)) + body
    if case == "mesh":
        n = 1_200_000
        tri = rng.random((n, 12), dtype=np.float32)
        body = b"".join(t.tobytes() + b"\x00\x00" for t in tri)
        return "big.stl", b"\x00" * 80 + struct.pack("<I", n) + body
    if case == "geo":
        coords = [[round(float(x), 6), round(float(y), 6)] for x, y in rng.random((600_000, 2))]
        return "track.geojson", json.dumps({"type": "FeatureCollection", "features": [
            {"type": "Feature", "geometry": {"type": "LineString", "coordinates": coords}, "properties": {}}]}).encode()
    if case == "tree":
        return "big.xml", b"<?xml version='1.0'?><r>" + b"".join(b"<e i='%d'>%d</e>" % (i, i) for i in range(400_000)) \
            + b"</r>"
    if case == "code":
        return "big.py", b"".join(b"def f%d(x):\n    return f%d(x) + %d\n\n" % (i, max(0, i - 1), i)
                                  for i in range(8_000))
    if case == "archive":
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w", zipfile.ZIP_STORED) as z:
            for i in range(4000):
                z.writestr(f"part/{i}.bin", rng.bytes(15_000))
        return "many.zip", out.getvalue()
    if case == "array":
        buf = io.BytesIO()
        np.save(buf, rng.random((2_000_000, 4)))                                    # 61 MiB of float64
        return "big.npy", buf.getvalue()
    if case == "header_only":
        sof = b"\xff\xc0" + struct.pack(">HBHHB", 17, 8, 3000, 4000, 3) + b"\x01\x11\x00\x02\x11\x00\x03\x11\x00"
        return "photo.jpg", b"\xff\xd8" + sof + b"\xff\xda" + rng.bytes(60 * 2**20) + b"\xff\xd9"
    if case == "unknown":
        return "noise.bin", rng.bytes(63 * 2**20)
    raise ValueError(case)

case, folder = sys.argv[2], sys.argv[3]
name, data = build(case)
db = store.create(os.path.join(folder, "individual.sqlite"), 1.8e9)
base = rss()
start = time.perf_counter()
try:
    record = A.add(db, data, name=name, now=1.8e9)
    index = A.show(db, record.id)["index"]
    outcome = {"kind": (index.get("representation") or {}).get("kind"), "partial": index.get("partial"),
               "error": (index.get("error") or {}).get("reason")}
except Exception as error:
    outcome = {"refused": type(error).__name__, "reason": getattr(error, "reason", None),
               "rule": getattr(error, "rule", None)}
wall = time.perf_counter() - start
print(json.dumps({"case": case, "bytes": len(data), "wall_s": round(wall, 3),
                  "peak_rss_above_baseline_mib": round(max(0, rss() - base) / 2**20, 1), "outcome": outcome}))
"""

CASES = ("text", "table", "document", "image", "audio", "mesh", "geo", "tree", "code", "archive", "array",
         "header_only", "unknown")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=str(ROOT / "docs" / "quality" / "evidence" / "omnimind_int.json"))
    ap.add_argument("--families", default=",".join(CASES))
    args = ap.parse_args()
    rows = []
    for case in args.families.split(","):
        with tempfile.TemporaryDirectory(prefix="omni-int-cost-") as folder:
            proc = subprocess.run([sys.executable, "-c", CHILD, str(ROOT / "src"), case, folder],
                                  capture_output=True, text=True, timeout=600,
                                  env={**os.environ, "PYTHONHASHSEED": "0"})
            lines = [ln for ln in proc.stdout.splitlines() if ln.startswith("{")]
            row = json.loads(lines[-1]) if lines else {"case": case, "error": proc.stderr[-300:]}
        print(json.dumps(row))
        rows.append(row)
    out = Path(args.out)
    value = json.loads(out.read_text()) if out.exists() else {}
    value["add_cost_per_family"] = {
        "what": "artifacts.store.add per family at the size limits: one run each, a fresh process per case, on a "
                "shared loaded host (wall clock; peak RSS above the baseline after imports, VmHWM)",
        "how": "python scripts/omnimind_int_add_cost.py", "rows": rows}
    text = json.dumps(value, indent=1, sort_keys=True) + "\n"
    out.write_text(text)
    print(f"wrote {out} ({len(text.encode())} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
