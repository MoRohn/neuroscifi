"""Prepare the public speech corpora for scripts/voice_real_speech_eval.py (docs/voice/VOICE_VALIDATION.md).

Reads archives already downloaded and extracted under the datasets directory (default
~/.local/share/neuroscifi/datasets, OUTSIDE the repository), selects a fixed, seeded, speaker-balanced subset and
writes 16 kHz mono 16-bit WAV copies plus manifest.json under <datasets>/prepared/. Decoding and resampling are done
by ffmpeg; nothing from the downloads is executed. Audio never enters the repository.

EdAcc ships as Parquet: reading it needs pyarrow, which the product does not depend on, so run this script with a
separate tool environment (docs/voice/VOICE_VALIDATION.md, "Reproduce"), with `python -I`.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
import csv
import glob
import json
import os
from pathlib import Path
import random
import re
import subprocess

SEED = 20261121


def ffmpeg_to_wav(src, dst, *, channel=0, data=None):
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists() and dst.stat().st_size > 44:
        return
    cmd = ["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", "pipe:0" if data is not None else str(src),
           "-af", f"pan=mono|c0=c{channel},aresample=16000:resampler=soxr", "-ar", "16000", "-ac", "1",
           "-sample_fmt", "s16", str(dst)]
    subprocess.run(cmd, input=data, check=True, timeout=120)


def balanced(items, key, per_group, rng, limit=None):
    groups = defaultdict(list)
    for it in items:
        groups[key(it)].append(it)
    out = []
    for g in sorted(groups):
        pool = sorted(groups[g], key=lambda x: x["id"])
        rng.shuffle(pool)
        out += pool[:per_group]
    rng.shuffle(out)
    return out[:limit] if limit else out


def librispeech(root, subset, per_speaker, rng):
    items = []
    for trans in sorted(glob.glob(str(root / "LibriSpeech" / subset / "*" / "*" / "*.trans.txt"))):
        folder = Path(trans).parent
        for line in Path(trans).read_text().splitlines():
            uid, text = line.split(" ", 1)
            items.append({"id": uid, "speaker": uid.split("-")[0], "text": text, "src": str(folder / f"{uid}.flac")})
    return balanced(items, lambda x: x["speaker"], per_speaker, rng)


def openslr83(root, per_group, rng):
    index = {}
    with open(root / "line_index_all.csv", newline="") as fh:
        for row in csv.reader(fh):
            if len(row) >= 3:
                index[row[1].strip()] = row[2].strip()
    items = []
    for wav in sorted(glob.glob(str(root / "*" / "*.wav"))):
        uid = Path(wav).stem
        if uid in index:
            group = Path(wav).parent.name
            items.append({"id": uid, "speaker": uid.split("_")[1], "group": group, "text": index[uid], "src": wav})
    return balanced(items, lambda x: x["group"], per_group, rng)


def edacc(root, per_l1, rng):
    import pyarrow.parquet as pq
    rows = []
    for path in sorted(glob.glob(str(root / "*.parquet"))):
        t = pq.read_table(path, columns=["speaker", "text", "l1", "accent"])
        for i, r in enumerate(t.to_pylist()):
            text = r["text"] or ""
            words = text.split()
            if "IGNORE" in text or "<" in text or "(" in text or not 4 <= len(words) <= 40:
                continue
            rows.append({"id": f"{Path(path).stem[:10]}-{i:05d}", "speaker": r["speaker"], "l1": r["l1"],
                         "accent": r["accent"], "text": text, "parquet": path, "row": i})
    return balanced(rows, lambda x: x["l1"], per_l1, rng)


def edacc_audio(items):
    import pyarrow.parquet as pq
    by_file = defaultdict(list)
    for it in items:
        by_file[it["parquet"]].append(it)
    out = {}
    for path, its in by_file.items():
        col = pq.read_table(path, columns=["audio"]).column("audio")
        for it in its:
            out[it["id"]] = col[it["row"]].as_py()["bytes"]
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--datasets", default=os.path.expanduser("~/.local/share/neuroscifi/datasets"))
    ap.add_argument("--jobs", type=int, default=16)
    a = ap.parse_args(argv)
    root = Path(a.datasets)
    out = root / "prepared"
    rng = random.Random(SEED)
    manifest = {"seed": SEED, "speech": {}, "noise": [], "music": [], "rir": [], "commands": []}
    jobs = []

    def speech(name, items, extra=()):
        recs = []
        for it in items:
            dst = out / name / f"{it['id']}.wav"
            if "src" in it:
                jobs.append((it["src"], dst, 0))
            recs.append({"id": it["id"], "speaker": it["speaker"], "text": it["text"], "wav": str(dst.relative_to(out)),
                         **{k: it[k] for k in extra}})
        manifest["speech"][name] = recs

    speech("librispeech_clean", librispeech(root / "librispeech", "test-clean", 8, rng))
    speech("librispeech_other", librispeech(root / "librispeech", "test-other", 8, rng))
    speech("openslr83_dialects", openslr83(root / "openslr83", 60, rng), extra=("group",))
    ed = edacc(root / "edacc", 25, rng)
    speech("edacc_accents", ed, extra=("l1",))

    for env in sorted(p.name for p in (root / "demand").iterdir() if p.is_dir()):
        manifest["noise"].append({"id": f"demand-{env}", "kind": "demand", "env": env,
                                  "wav": str(Path("..") / "demand" / env / "ch01.wav")})
    real = root / "rirs_noises" / "RIRS_NOISES" / "real_rirs_isotropic_noises"
    for f in sorted(real.glob("RVB2014_type1_noise_*_1.wav")):
        dst = out / "noise" / f.name
        jobs.append((str(f), dst, 0))
        manifest["noise"].append({"id": f.stem, "kind": "isotropic", "env": f.stem, "wav": str(dst.relative_to(out))})
    annotations = {}
    ann = root / "musan" / "musan" / "music" / "fma" / "ANNOTATIONS"
    if ann.exists():
        for line in ann.read_text().splitlines():
            parts = line.split()
            if len(parts) >= 3:
                annotations[parts[0]] = parts[2] == "Y"
    for f in sorted((root / "musan" / "musan" / "music" / "fma").glob("*.wav")):
        if f.stat().st_size > 16000 * 2 * 20:        # complete files only (the subset was streamed)
            manifest["music"].append({"id": f.stem, "vocals": annotations.get(f.stem),
                                      "wav": str(Path("..") / "musan" / "musan" / "music" / "fma" / f.name)})
    manifest["music"] = manifest["music"][:-1]        # the last streamed file may be truncated
    for f in sorted(real.glob("RVB2014_type1_rir_*.wav")):
        m = re.search(r"rir_(\w+?room\d)_(near|far)_", f.name)
        dst = out / "rir" / f.name
        jobs.append((str(f), dst, 0))
        manifest["rir"].append({"id": f.stem, "kind": "real", "room": m.group(1), "distance": m.group(2),
                                "wav": str(dst.relative_to(out))})
    for f in sorted(real.glob("air_type1_air_binaural_*.wav"))[::4]:
        dst = out / "rir" / f.name
        jobs.append((str(f), dst, 0))
        manifest["rir"].append({"id": f.stem, "kind": "real", "room": "air_" + f.name.split("_")[4],
                                "distance": "near", "wav": str(dst.relative_to(out))})
    sim = root / "rirs_noises" / "RIRS_NOISES" / "simulated_rirs"
    for size in ("smallroom", "mediumroom", "largeroom"):
        files = sorted(sim.glob(f"{size}/Room0[0-4]?/*.wav"))
        for f in random.Random(SEED).sample(files, min(30, len(files))):
            manifest["rir"].append({"id": f"sim-{size}-{f.parent.name}-{f.stem}", "kind": "simulated", "room": size,
                                    "distance": "far" if size == "largeroom" else "near",
                                    "wav": str(Path("..") / f.relative_to(root))})
    sc = root / "speech_commands" / "raw"
    want = {"stop": 150, "no": 60, "yes": 60, "go": 40, "up": 20, "down": 20, "left": 20, "right": 20, "on": 20,
            "off": 20}
    for word, n in want.items():
        files = sorted((sc / word).glob("*.wav"))
        for f in random.Random(SEED + len(word)).sample(files, min(n, len(files))):
            manifest["commands"].append({"id": f"sc-{word}-{f.stem}", "speaker": f.stem.split("_")[0], "text": word,
                                         "wav": str(Path("..") / f.relative_to(root))})

    with ThreadPoolExecutor(a.jobs) as pool:
        list(pool.map(lambda j: ffmpeg_to_wav(Path(j[0]), j[1], channel=j[2]), jobs))
    audio = edacc_audio(ed)
    with ThreadPoolExecutor(a.jobs) as pool:
        list(pool.map(lambda it: ffmpeg_to_wav(None, out / "edacc_accents" / f"{it['id']}.wav", data=audio[it["id"]]),
                      ed))
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1))
    counts = {k: len(v) for k, v in manifest["speech"].items()}
    print(json.dumps({"speech": counts, "noise": len(manifest["noise"]), "music": len(manifest["music"]),
                      "rir": len(manifest["rir"]), "commands": len(manifest["commands"])}))


if __name__ == "__main__":
    main()
