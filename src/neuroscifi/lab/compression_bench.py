"""Measure complete archives with the same framing and bounded chunks."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import platform
import random
import sqlite3
import statistics
import tempfile
from time import perf_counter_ns
import zstandard
import numpy as np
from ..archive import METHODS, FAST, COLD, TRIAL, pack, unpack
from .graph_bench import world
from ..legacy_live import open_store


def repo_root():
    for d in Path(__file__).resolve().parents:
        if (d/"pyproject.toml").is_file() and (d/"evidence").is_dir():
            return d
    raise SystemExit("compression_bench needs the NeuroSciFi repository checkout (evidence/ and research/); run from an editable install of the repo.")


def inputs():
    rng = np.random.default_rng(43817)
    frames = [world(rng)[0] for _ in range(48)]
    graph = np.rint(np.stack(frames)*127/.75).astype("i1").tobytes()
    increments = rng.choice([-2,-1,0,1,2], size=32769, p=[.05,.20,.50,.20,.05])
    sensor = np.cumsum(increments).astype("<i2").tobytes()
    with tempfile.TemporaryDirectory() as temp:
        db = open_store(Path(temp)/"learner.sqlite")
        with db:
            db.execute("INSERT INTO epochs VALUES ('desk')")
            for i in range(1600):
                channel = ("camera","word","reference")[i%3]
                db.execute("INSERT INTO observations VALUES (?,?,?,?,?,?)",
                           (f"obs-{i:08}", "desk", channel, i%2, int(rng.integers(2)), channel+"-main"))
        target = sqlite3.connect(Path(temp)/"snapshot.sqlite")
        db.backup(target)
        target.close()
        db.close()
        snapshot = (Path(temp)/"snapshot.sqlite").read_bytes()
    return {"model_results_json":(repo_root()/"evidence/graph.json").read_bytes(),
            "cpu_timings_csv":(repo_root()/"research/results/bench_915.csv").read_bytes(),
            "learner_sqlite":snapshot, "dense_layout_int8_graphs":graph,
            "smooth_pcm16":sensor, "uniform_bytes":rng.bytes(65536)}


def run(repeats):
    groups = {name:(name,) for name in METHODS}
    groups.update({"fast":FAST, "cold":COLD, "experimental":TRIAL})
    results = {}
    for name, raw in inputs().items():
        records = {k:[] for k in groups}
        for rep in range(repeats):
            order = list(groups)
            random.Random(199 + rep).shuffle(order)
            for key in order:
                dst = io.BytesIO()
                t0 = perf_counter_ns()
                modes = pack(io.BytesIO(raw), dst, groups[key])
                packed = dst.getvalue()
                t1 = perf_counter_ns()
                restored = io.BytesIO()
                unpack(io.BytesIO(packed), restored)
                t2 = perf_counter_ns()
                if restored.getvalue() != raw:
                    raise ValueError("Archive round trip failed for "+name+" / "+key)
                records[key].append((len(packed), (t1-t0)/1e6, (t2-t1)/1e6, modes))
        results[name] = {"raw_bytes":len(raw),"source_sha256":hashlib.sha256(raw).hexdigest(),"profiles":{}}
        for key,rows in records.items():
            assert len({r[0] for r in rows}) == 1
            results[name]["profiles"][key] = {"archive_bytes":rows[0][0],
                "ratio":len(raw)/rows[0][0],"median_encode_ms":statistics.median(r[1] for r in rows),
                "median_decode_ms":statistics.median(r[2] for r in rows),"blocks":rows[0][3]}
    return {"repeats":repeats,"source":"original CPU measurements","machine":platform.machine(),
            "sqlite":sqlite3.sqlite_version,"numpy":np.__version__,"zstandard":zstandard.__version__,"results":results}


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--repeats",type=int,default=3)
    p.add_argument("--output",type=Path)
    a = p.parse_args()
    if a.repeats < 1:
        p.error("Choose at least one repetition")
    payload = json.dumps(run(a.repeats),indent=2)+"\n"
    if a.output:
        a.output.write_text(payload)
    else:
        print(payload,end="")
