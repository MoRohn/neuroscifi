"""Lane B2's evidence run (not collected by pytest): writes docs/quality/evidence/omnimind_b2.json.

Per adapter module: the corpus sample's outcome, random-corruption outcomes (adapter called directly), and the
time and peak resident memory (RSS growth during the decode, measured in a fresh process) on the largest honest
input and the worst hostile input built here. Run from the worktree root:

    PYTHONPATH=$PWD/src python tests/artifacts/b2_evidence.py
"""
from __future__ import annotations

import collections
import io
import json
import os
import random
import struct
import subprocess
import sys
import tempfile
import time
import zipfile
import zlib

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)

CHILD = r"""
import json, sys, time


def hwm():
    # peak RSS of this process image in KiB (VmHWM; ru_maxrss would carry the parent's peak across fork+exec)
    for line in open("/proc/self/status"):
        if line.startswith("VmHWM:"):
            return int(line.split()[1])
    return 0


sys.path.insert(0, sys.argv[2])
from neuroscifi.artifacts import adapters, types as T
from neuroscifi.artifacts.adapters import _b2
_b2.register_all()
data = open(sys.argv[1], "rb").read()
base = hwm()
t = time.perf_counter()
try:
    out = adapters.decode(data)
    outcome = "decoded: " + type(out.representation).__name__ + (" (partial)" if out.partial else "")
except T.ArtifactError as e:
    outcome = f"{type(e).__name__} ({getattr(e, 'rule', e.reason)})"
dt = time.perf_counter() - t
peak = hwm()
print(json.dumps({"outcome": outcome, "seconds": round(dt, 3), "rss_growth_mib": round((peak - base) / 1024, 1)}))
"""


def measure(data: bytes) -> dict:
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "input.bin")
        with open(path, "wb") as f:
            f.write(data)
        child = os.path.join(d, "child.py")
        with open(child, "w") as f:
            f.write(CHILD)
        env = {**os.environ, "PYTHONPATH": os.path.join(ROOT, "src")}
        res = subprocess.run([sys.executable, "-I", child, path, os.path.join(ROOT, "src")], capture_output=True,
                             text=True, timeout=600, env=env)
        if res.returncode:
            return {"outcome": "child failed", "stderr_tail": res.stderr[-300:]}
        out = json.loads(res.stdout)
        out["input_mib"] = round(len(data) / (1 << 20), 2)
        return out


def _chunk(ctype, body):
    return struct.pack(">I", len(body)) + ctype + body + struct.pack(">I", zlib.crc32(ctype + body))


def _png_raw(w, h, rows, ctype=2, bits=8, level=1):
    return (b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, bits, ctype, 0, 0, 0))
            + _chunk(b"IDAT", zlib.compress(rows, level)) + _chunk(b"IEND", b""))


def cases(rng):
    from b2_fixtures import fmt_chunk, riff
    from test_b2_arrays import _saved
    from test_b2_headers import _mtrk, _tiff_image
    from test_b2_rasters import _gif
    out = {}
    # PNG: 2000 x 2000 RGB, every row Paeth-filtered random bytes / a zero stream within the budget (64 MiB)
    rows = np.concatenate([np.full((2000, 1), 4, np.uint8), rng.integers(0, 256, (2000, 6000), dtype=np.uint8)], 1)
    out["png"] = (_png_raw(2000, 2000, rows.tobytes()), _png_raw(8000, 8000, bytes(8001 * 8000), ctype=0, level=9))
    big = b"P6 3000 3000 255\n" + rng.integers(0, 256, 3000 * 3000 * 3, dtype=np.uint8).tobytes()
    runs = b"qoif" + struct.pack(">IIBB", 4096, 4096, 4, 0) + b"\xfd" * (4096 * 4096 // 62 + 1) + b"\x00" * 7 + b"\x01"
    out["netpbm"] = (big, runs)
    w = h = 3000
    bmp_rows = rng.integers(0, 256, (h, w * 3), dtype=np.uint8).tobytes()
    info = struct.pack("<IiiHHIIiiII", 40, w, h, 1, 24, 0, len(bmp_rows), 0, 0, 0, 0)
    bmp = b"BM" + struct.pack("<IHHI", 54 + len(bmp_rows), 0, 0, 54) + info + bmp_rows
    rle = (bytes([255, 1]) * 17 + b"\x00\x00") * 4096 + b"\x00\x01"
    info = struct.pack("<IiiHHIIiiII", 40, 4096, 4096, 1, 8, 1, len(rle), 0, 0, 2, 0)
    pal = b"\x00\x00\x00\x00\xff\xff\xff\x00"
    rle_bmp = b"BM" + struct.pack("<IHHI", 62 + len(rle), 0, 0, 62) + info + pal + rle
    out["bmp"] = (bmp, rle_bmp)
    idx = rng.integers(0, 256, 1000 * 1000).tolist()
    pal256 = [(i, i, i) for i in range(256)]
    out["gif"] = (_gif(1000, 1000, [(0, 0, 1000, 1000, idx, 0, 0, None, False)], pal256),
                  _gif(4000, 4000, [(0, 0, 4000, 4000, [0] * (4000 * 4000), 0, 0, None, False)], pal256[:2]))
    tiff = _tiff_image(3000, 3000, rng.integers(0, 256, 3000 * 3000 * 3, dtype=np.uint8).tobytes(), comp=32773)
    loop = b"II*\x00" + struct.pack("<I", 8) + struct.pack("<H", 4096) + b"\x00" * (12 * 4096) + struct.pack("<I", 8)
    out["image_headers"] = (tiff, loop)
    shapes = "".join(f'<path d="M{i} 0 C {i} 5 {i + 5} 5 {i + 5} 0 A 3 3 0 0 1 {i + 9} 0"/>' for i in range(20000))
    svg = f'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10">{shapes}</svg>'.encode()
    bomb = ['<svg xmlns="http://www.w3.org/2000/svg"><defs><g id="l0"><rect width="1" height="1"/></g>']
    for i in range(1, 9):
        bomb.append(f'<g id="l{i}">' + f'<use href="#l{i - 1}"/>' * 10 + "</g>")
    bomb.append('</defs><use href="#l8"/></svg>')
    out["svg"] = (svg, "".join(bomb).encode())
    n = 44100 * 300
    wav = riff(b"WAVE", [(b"fmt ", fmt_chunk(1, 2, 44100, 16)),
                         (b"data", rng.integers(-32768, 32767, n * 2, dtype=np.int16).tobytes())])
    many = riff(b"WAVE", [(b"fmt ", fmt_chunk(1, 1, 8000, 16))] + [(b"junk", b"")] * 100_000)
    out["wav"] = (wav, many)
    frame = bytearray(417)
    frame[:4] = b"\xff\xfb\x90\x64"
    adts = bytes((0xFF, 0xF1, 0x50, 0x80, 0x00, 0xFF, 0xFC))         # 7-byte frames: the longest walk per byte
    out["audio_headers"] = (bytes(frame) * 150_000, adts * 1_200_000)
    events = []
    for i in range(100_000):
        events += [(10, bytes((0x90, 60 + i % 12, 64))), (10, bytes((0x80, 60 + i % 12, 0)))]
    midi = b"MThd" + struct.pack(">IHHH", 6, 0, 1, 96) + _mtrk(events)
    flood = b"MThd" + struct.pack(">IHHH", 6, 0, 1, 96) + _mtrk([(0, b"\xff\x01\x00")] * 6_000_000)
    out["midi"] = (midi, flood)
    from test_b2_headers import _mp4
    mp4 = _mp4() + struct.pack(">I", 8 + 50_000_000) + b"mdat" + bytes(50_000_000)
    flat = struct.pack(">I", 8 + 8 * 200_000) + b"moov" + (struct.pack(">I", 8) + b"free") * 200_000
    out["video_containers"] = (mp4, flat)
    recs = np.zeros(1_000_000, dtype=[("n", "<f4", 3), ("v", "<f4", (3, 3)), ("a", "<u2")])
    recs["v"] = rng.random((1_000_000, 3, 3)).astype(np.float32)
    stl = b"\0" * 80 + struct.pack("<I", 1_000_000) + recs.tobytes()
    out["meshes"] = (stl, b"v 0 0 0\n" * 8_000_000)
    pos = rng.random((500_000, 3)).astype("<f4").tobytes()
    idx = np.arange(1_500_000, dtype="<u4") % 500_000
    blob = pos + idx.tobytes()
    doc = {"asset": {"version": "2.0"}, "meshes": [{"primitives": [{"attributes": {"POSITION": 0}, "indices": 1}]}],
           "accessors": [{"bufferView": 0, "componentType": 5126, "count": 500_000, "type": "VEC3"},
                         {"bufferView": 1, "componentType": 5125, "count": 1_500_000, "type": "SCALAR"}],
           "bufferViews": [{"buffer": 0, "byteOffset": 0, "byteLength": len(pos)},
                           {"buffer": 0, "byteOffset": len(pos), "byteLength": len(idx) * 4}],
           "buffers": [{"byteLength": len(blob)}]}
    js = json.dumps(doc).encode()
    js += b" " * (-len(js) % 4)
    body = struct.pack("<II", len(js), 0x4E4F534A) + js + struct.pack("<II", len(blob), 0x004E4942) + blob
    glb = b"glTF" + struct.pack("<II", 2, 12 + len(body)) + body
    doc_h = dict(doc)
    doc_h["accessors"] = [{"bufferView": 0, "componentType": 5126, "count": 10 ** 12, "type": "MAT4"}]
    doc_h["meshes"] = [{"primitives": [{"attributes": {"POSITION": 0}}]}] * 1000
    out["gltf"] = (glb, json.dumps(doc_h).encode())
    coords = rng.random((1_000_000, 2)).round(6).tolist()
    geo = json.dumps({"type": "LineString", "coordinates": coords}).encode()
    deep = b'{"type": "Point", "coordinates": ' + b"[" * 200_000 + b"]" * 200_000 + b"}"
    out["geo"] = (geo, deep)
    npy = _saved(rng.random((60 << 20) // 8 // 64 * 8).reshape(-1, 8))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("z.npy", _saved(np.zeros(20_000_000)))
    out["arrays"] = (npy, buf.getvalue())
    return out


def corruption(samples, seeds=4, per=400):
    from neuroscifi.artifacts import adapters
    from neuroscifi.artifacts import types as T
    from neuroscifi.artifacts.types import HeaderOnly
    stats = collections.defaultdict(collections.Counter)
    for seed in range(seeds):
        for name, (fmt, good) in sorted(samples.items()):
            rnd = random.Random(seed * 1000 + len(name))
            det = T.Detected("unknown", fmt, "x", "signature", None, ())
            adapter = adapters.resolve(det)
            for trial in range(per):
                bad = bytearray(good)
                m = trial % 3
                if m == 0:
                    for _ in range(rnd.randint(1, 6)):
                        bad[rnd.randrange(len(bad))] = rnd.randrange(256)
                elif m == 1:
                    bad = bad[:rnd.randrange(1, len(bad))]
                else:
                    at = rnd.randrange(len(bad))
                    bad[at:at] = bytes(rnd.randrange(256) for _ in range(rnd.randint(1, 16)))
                key = adapter.name
                try:
                    adapter.decode(bytes(bad), det, T.Limits(max_seconds=5))
                    stats[key]["decoded"] += 1
                except HeaderOnly:
                    stats[key]["header_only"] += 1
                except T.ArtifactError as e:
                    stats[key][type(e).__name__.lower()] += 1
                except Exception as e:             # counted, never expected: the registry would name it Malformed
                    stats[key]["unnamed_" + type(e).__name__] += 1
    return {k: dict(v) for k, v in sorted(stats.items())}


def main():
    from b2_corpus import samples
    from neuroscifi.artifacts import adapters
    from neuroscifi.artifacts.adapters import _b2
    from neuroscifi.artifacts.types import HeaderOnly
    _b2.register_all()
    rng = np.random.default_rng(20261126)
    corpus = samples()
    sample_outcomes = {}
    for name, (fmt, data) in sorted(corpus.items()):
        try:
            out = adapters.decode(data)
            sample_outcomes[name] = "decoded: " + type(out.representation).__name__
        except HeaderOnly as e:
            sample_outcomes[name] = "header only; needs " + "; ".join(e.needs)
    t = time.time()
    fuzz = corruption(corpus)
    fuzz_seconds = round(time.time() - t, 1)
    big = {}
    for module, (honest, hostile) in cases(rng).items():
        big[module] = {"largest_honest": measure(honest), "worst_hostile": measure(hostile)}
        print(module, big[module], flush=True)
    evidence = {
        "lane": "B2", "date": "2026-11-26", "host": os.uname().machine, "python": sys.version.split()[0],
        "numpy": np.__version__,
        "limits": "types.Limits() defaults (64 MiB input, 256 MiB decoded, 5M elements, 4096 members, 20 s); "
                  "corruption trials use max_seconds 5",
        "corpus_samples": sample_outcomes,
        "corruption": {"trials_per_sample": 4 * 400, "samples": len(corpus), "seconds": fuzz_seconds,
                       "by_adapter": fuzz},
        "large_inputs": big,
        "method": "large inputs are decoded in a fresh python -I process; rss_growth_mib is the process's peak RSS "
                  "(VmHWM) after the decode minus before it (the input bytes are already resident); seconds is wall clock",
    }
    path = os.path.join(ROOT, "docs", "quality", "evidence", "omnimind_b2.json")
    with open(path, "w") as f:
        json.dump(evidence, f, indent=1, sort_keys=True)
        f.write("\n")
    print("wrote", path, os.path.getsize(path), "bytes")


if __name__ == "__main__":
    main()
