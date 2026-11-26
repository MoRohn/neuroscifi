"""Family `unknown`: identification without the usual hints, in both directions.

- Disguised (decodable): a real file under a wrong or missing extension, a wrong declared type, a byte-order mark,
  UTF-16, a gzip or zstd wrapper, two wrappers, or an unusual but valid variant (ancillary PNG chunks with the image
  data split over several IDAT chunks, a WAV with a LIST chunk and the extensible header, a PGM full of comments, a
  binary STL whose 80-byte header starts with "solid", a top-down BMP, NDJSON named .txt, GeoJSON declared as plain
  JSON, an ISO-8859-1 XML). Questions: the true format and a content measurement. An honest "cannot" here is a
  MISSED decodable file (reported as `missed_decodable`).
- Truly unknown: seeded random bytes, an invented record format, an invented "encrypted" container, all zeros, random
  bytes with a PNG signature buried at offset 100, and the first bytes of real formats (whose signature IS
  identifiable). Questions: the format ("unknown", except a truncated head, where the real name or "unknown" are
  both right), the family, and a content question where only an abstention is right. Naming a format that is not
  there is a FALSE IDENTIFICATION (reported as `false_identification`).
"""
from __future__ import annotations

import struct
import time

import numpy as np

from . import Task, check_split, enc, run_tasks, summarise
from .cases import UNKNOWN
from .intake import (ONE_OF, REL, csv_bytes, gen_json_tree, gen_png, gen_wav, pixels, q, rng_for, table_values,
                     word)


def _csv(rng, rows=7):
    t = table_values(rng, {"columns": ("unit", "reading", "count", "label"), "rows": rows})
    return t, csv_bytes(t)


def _table_qs(t):
    n = len(t["cols"][0])
    return [q("table.n_rows", n), q(f"table.column_mean:{t['names'][2]}", sum(t["cols"][2]) / n, REL)]


def build(case) -> tuple[dict, dict, list]:
    """(files, declared, questions) for one case."""
    make, rng, name = case["make"], rng_for(case["seed"]), case["file"]
    declared = {name: case["declared"]} if case.get("declared") else {}
    if make == "csv":
        t, data = _csv(rng)
        return {name: data}, declared, [q("detect.format", "csv"), *_table_qs(t)]
    if make == "csv_utf16":
        t, data = _csv(rng)
        return ({name: b"\xff\xfe" + data.decode().encode("utf-16-le")}, declared,
                [q("detect.format", "csv"), *_table_qs(t)])
    if make == "json_bom":
        files, _, qs = gen_json_tree({"items": 2, "readings": 3, "file": "x"}, rng)
        data = b"\xef\xbb\xbf" + next(iter(files.values()))
        return {name: data}, declared, [qq for qq in qs if qq[0] != "detect.family"]
    if make == "png":
        files, _, qs = gen_png({"mode": "rgb", "bits": 8, "size": (7, 5), "file": "x"}, rng)
        return {name: next(iter(files.values()))}, declared, [qq for qq in qs if qq[0].startswith(("detect.f",
                                                                                                    "image.size"))]
    if make in ("csv_gzip", "csv_zstd", "csv_gzip_zstd"):
        t, data = _csv(rng, 9)
        if make in ("csv_zstd", "csv_gzip_zstd"):
            data = enc.zstd_raw_frame(data)
        if make in ("csv_gzip", "csv_gzip_zstd"):
            data = enc.gzip_member(data)
        outer = {"csv_gzip": "gzip", "csv_zstd": "zstd", "csv_gzip_zstd": "gzip"}[make]
        return {name: data}, declared, [q("detect.format", [outer, "zst"] if outer == "zstd" else [outer], ONE_OF),
                                        *_table_qs(t)]
    if make == "zip_in_gzip":
        t, data = _csv(rng, 5)
        inner = enc.zip_files({"data.csv": data, "notes.txt": b"plain\n"})
        return ({name: enc.gzip_member(inner)}, declared,
                [q("detect.format", "gzip"), q("data.csv::table.n_rows", 5)])
    if make == "png_ancillary":
        arr = pixels(rng, 9, 8, 1, 255)
        data = enc.png(arr, mode="gray", extra_chunks=((b"gAMA", struct.pack(">I", 45455)),
                                                       (b"tEXt", b"Comment\x00made by hand"),
                                                       (b"pHYs", struct.pack(">IIB", 2835, 2835, 1))), idat_parts=3)
        return ({name: data}, declared, [q("detect.format", "png"), q("image.size", [8, 9]),
                                         q("image.mean_gray", float(arr.mean() / 255), {"abs": 1e-6})])
    if make == "wav_list":
        files, _, qs = gen_wav({"rate": 16000, "bits": 16, "channels": 1, "freq": 350.0, "bursts": 2,
                                "burst_s": 0.2, "gap_s": 0.2, "file": "x"}, rng)
        data = next(iter(files.values()))
        fmt_end = data.index(b"data")
        info = b"INFO" + b"INAM" + struct.pack("<I", 6) + b"bench\x00"
        data = data[:fmt_end] + b"LIST" + struct.pack("<I", len(info)) + info + data[fmt_end:]
        data = data[:4] + struct.pack("<I", len(data) - 8) + data[8:]
        return {name: data}, declared, [qq for qq in qs if qq[0] in ("detect.format", "audio.duration_s",
                                                                     "audio.n_onsets")]
    if make == "pgm_comments":
        arr = pixels(rng, 4, 5, 1, 255)
        head = b"P5\n# one\n# two\n5 # width\n4\n# max next\n255\n"
        return ({name: head + arr.astype(np.uint8).tobytes()}, declared,
                [q("detect.format", ["pgm", "pnm"], ONE_OF), q("image.size", [5, 4]),
                 q("image.pixel:4,3", [int(arr[3, 4])])])
    if make == "stl_solid_header":
        verts = np.array([[0, 0, 0], [2, 0, 0], [0, 2, 0], [0, 0, 2]], float)
        tris = [[0, 2, 1], [0, 1, 3], [0, 3, 2], [1, 2, 3]]
        data = enc.stl_binary(verts, tris, header=b"solid looks like ascii but is binary")
        return {name: data}, declared, [q("detect.format", "stl"), q("mesh.n_faces", 4),
                                        q("mesh.volume", 8 / 6, {"rel": 1e-6})]
    if make == "bmp_top_down":
        arr = pixels(rng, 3, 4, 3, 255)
        return ({name: enc.bmp(arr, top_down=True)}, declared,
                [q("detect.format", "bmp"), q("image.pixel:0,0", [int(v) for v in arr[0, 0]])])
    if make == "ndjson_txt":
        rows = [{"k": word(rng), "v": int(rng.integers(100))} for _ in range(6)]
        import json
        data = ("\n".join(json.dumps(r) for r in rows) + "\n").encode()
        return ({name: data}, declared, [q("detect.format", ["ndjson", "jsonl", "jsonlines", "json-lines"], ONE_OF),
                                         q("table.n_rows", 6), q("table.column_mean:v", sum(r["v"] for r in rows) / 6,
                                                                 REL)])
    if make == "geojson_as_json":
        import json
        pts = [[round(float(rng.uniform(-5, 5)), 4), round(float(rng.uniform(40, 45)), 4)] for _ in range(3)]
        doc = {"type": "FeatureCollection", "features": [
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": p}, "properties": {}} for p in pts]}
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        return ({name: json.dumps(doc).encode()}, declared,
                [q("detect.format", "geojson"), q("geo.bbox", [min(xs), min(ys), max(xs), max(ys)], {"abs": 1e-9})])
    if make == "xml_latin1":
        label = "café " + word(rng)
        data = f'<?xml version="1.0" encoding="ISO-8859-1"?>\n<menu><item>{label}</item></menu>\n'.encode("latin-1")
        return {name: data}, declared, [q("detect.format", "xml"), q("tree.value:item", label)]
    # ---- truly unknown ----
    content = [q("table.n_rows", None, None, "cannot"), q("image.size", None, None, "cannot")]
    unknown = [q("detect.format", "unknown"), q("detect.family", "unknown")]
    if make == "random":
        return {name: rng.integers(0, 256, case["size"]).astype(np.uint8).tobytes()}, declared, unknown + content
    if make == "records":
        recs = b"".join(struct.pack("<IfI", i, float(rng.normal()), int(rng.integers(8))) for i in range(case["n"]))
        return {name: case["magic"] + struct.pack("<I", case["n"]) + recs}, declared, unknown + content
    if make == "sealed":
        body = rng.integers(0, 256, case["size"]).astype(np.uint8).tobytes()
        return {name: case["magic"] + bytes(rng.integers(0, 256, 16).astype(np.uint8)) + body}, declared, \
            unknown + content
    if make == "zeros":
        return {name: bytes(case["size"])}, declared, unknown + content
    if make == "buried_signature":
        noise = bytearray(rng.integers(0, 256, case["size"]).astype(np.uint8).tobytes())
        noise[100:108] = b"\x89PNG\r\n\x1a\n"
        return {name: bytes(noise)}, declared, unknown + content
    if make == "head_of_png":
        files, _, _ = gen_png({"mode": "gray", "bits": 8, "size": (16, 16), "file": "x"}, rng)
        head = next(iter(files.values()))[:case["keep"]]
        return ({name: head}, declared, [q("detect.format", ["png", "unknown"], ONE_OF),
                                         q("image.mean_gray", None, None, "cannot")])
    if make == "head_of_zip":
        data = enc.zip_files({"a.txt": b"alpha " * 50, "b.txt": b"beta " * 50}, method="store")
        return ({name: data[:case["keep"]]}, declared, [q("detect.format", ["zip", "unknown"], ONE_OF),
                                                        q("bundle.members", None, None, "cannot")])
    raise ValueError(make)


def tasks(split: str) -> list[Task]:
    check_split(split)
    out = []
    for case in UNKNOWN[split]:
        files, declared, questions = build(case)
        for question, expect, tolerance, kind in questions:
            out.append(Task(f"unknown/{split}/{case['name']}/{question}", "unknown", files, declared, question,
                            expect, tolerance, kind))
    return out


def _is_disguised(task_id: str, split: str) -> bool:
    name = task_id.split("/")[2]
    return next(c for c in UNKNOWN[split] if c["name"] == name)["kind"] == "disguised"


def score(subject, split: str) -> dict:
    start = time.perf_counter()
    rows = run_tasks("unknown", split, subject, tasks(split))
    disguised = [(t, r) for t, r in rows if _is_disguised(t.id, split)]
    truly = [(t, r) for t, r in rows if not _is_disguised(t.id, split)]
    missed = sum(r.outcome == "abstained" for t, r in disguised)
    false_id = sum(r.outcome == "wrong" for t, r in truly if t.question.startswith("detect."))
    n_id = sum(1 for t, _ in truly if t.question.startswith("detect."))
    return summarise("unknown", split, subject, rows, time.perf_counter() - start,
                     {"disguised": {"n": len(disguised), "right": sum(r.outcome == "right" for _, r in disguised),
                                    "missed_decodable": missed,
                                    "missed_decodable_rate": round(missed / len(disguised), 4) if disguised else None},
                      "truly_unknown": {"n": len(truly), "right": sum(r.outcome == "right" for _, r in truly),
                                        "false_identification": false_id,
                                        "false_identification_rate": round(false_id / n_id, 4) if n_id else None}})
