"""Cost of imagined.remove_naming (the forget-by-text scan, OMNIMIND lane S) at 10,000 imagined records, inside the
write transaction. Prints JSON; docs/quality/evidence/omnimind_s.json holds one run.

    PYTHONPATH=src python scripts/bench_omnimind_s_forget.py
"""
import base64
import json
import os
import platform
import statistics
import struct
import sys
import tempfile
import time
import zlib
from pathlib import Path
from neuroscifi.instance import imagined, store

T0 = 1_800_000_000.0


def png(extra):
    def chunk(k, b):
        return struct.pack(">I", len(b)) + k + b + struct.pack(">I", zlib.crc32(k + b))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 0, 0, 0, 0))
            + chunk(b"zTXt", b"Comment\x00\x00" + zlib.compress(b"nothing here")) + chunk(b"IDAT", extra)
            + chunk(b"IEND", b""))


def run(n, payload_of, label):
    with tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR")) as d:
        db = store.create(Path(d) / "m.sqlite", T0)
        store.set_config(db, {"max_imagined_rows": n})
        for i in range(n):
            imagined.record(db, "speculation", payload=payload_of(i), assumptions=[], uncertainty="u", mechanism="m",
                            mechanism_version="1", question="q", now=T0 + i)
        times = []
        for _ in range(3):
            db.execute("BEGIN IMMEDIATE")
            start = time.perf_counter()
            removed, _ = imagined.remove_naming(db, ["Alice-zq9"], T0 + n + 1)
            times.append(time.perf_counter() - start)
            db.execute("ROLLBACK")
        size = (Path(d) / "m.sqlite").stat().st_size
        db.close()
    return {"case": label, "records": n, "removed": removed, "file_bytes": size,
            "seconds_median": round(statistics.median(times), 3), "seconds_runs": [round(t, 3) for t in times]}


rng = bytes((i * 131 + 7) % 251 for i in range(30000))
out = {"what": "imagined.remove_naming over the whole store, inside a BEGIN IMMEDIATE write transaction (rolled back), "
               "3 runs each, nothing matching (every record fully scanned)",
       "host": platform.machine(), "python": sys.version.split()[0], "date": time.strftime("%Y-%m-%d"),
       "results": [
           run(10_000, lambda i: imagined.example(i)["payload"], "10,000 records of imagined.example payloads"),
           run(10_000, lambda i: {"image": "data:image/png;base64," + base64.b64encode(png(rng[:3000])).decode(),
                                  "n": i}, "10,000 records, each a 4 KiB base64 PNG with a zTXt chunk"),
           run(1_000, lambda i: {"image": base64.b64encode(png(rng + rng[:i])).decode()},
               "1,000 records, each a 40 KiB base64 PNG with a zTXt chunk"),
       ]}
print(json.dumps(out, indent=1))
