"""Lane FX's own cross pairs (item 3): fresh kinds, including pairs whose meaning nothing here can measure. Each
pair has a matching twin (truth True) and a non-matching twin (truth False) differing in one value. Scored as the
cross family does: right = answer == truth, cannot = None, wrong = the other boolean. Seeds 42000+.
usage: corpus_cross.py OUT.json  (runs in a child with a 3 GiB cap, items one at a time)"""
from __future__ import annotations

import csv
import io
import json
import resource
import sys
import zipfile

import numpy as np

from neuroscifi.acceptance.omni import enc


def png(w, h, seed):
    return enc.png(np.random.default_rng(seed).integers(0, 255, (h, w)).astype(np.uint8), mode="gray")


def wav(seconds, seed, rate=8000):
    t = np.arange(int(seconds * rate)) / rate
    return enc.wav(0.4 * np.sin(2 * np.pi * (300 + seed % 200) * t), rate)


def csv_amounts(values, header="amount"):
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["item", header])
    for i, v in enumerate(values):
        w.writerow([f"line {i + 1}", f"{v:.2f}"])
    return buf.getvalue().encode()


def build():
    items = []   # (kind, files, question, truth)

    def twin(kind, q, yes, no):
        items.append((kind, yes, q, True))
        items.append((kind, no, q, False))

    nouns = ["boats", "apples", "people", "birds", "cars", "trees"]
    content_qs = ["Does the caption describe the image?", "Is the caption right about the picture?",
                  "Does the photo show what its caption says?", "Does the caption match the image?",
                  "Is this the right caption for this picture?", "Is the caption true of this image?"]
    for i in range(6):
        w, h, n = 40 + 8 * i, 30 + 4 * i, 2 + i
        img = png(w, h, 42000 + i)
        q = content_qs[i]
        twin("image_caption_count", q, {"photo.png": img, "caption.txt": f"A {w} x {h} photo of {n} {nouns[i]}.\n".encode()},
             {"photo.png": img, "caption.txt": f"A {w} x {h} photo of {n + 3} {nouns[i]}.\n".encode()})
        twin("image_caption_colour", q, {"shot.png": img, "caption.md": f"# Figure\n\nA red circle on white, {w} x {h} pixels.\n".encode()},
             {"shot.png": img, "caption.md": f"# Figure\n\nA blue circle on white, {w} x {h} pixels.\n".encode()})
        twin("image_title_unrelated_numbers", q, {"photo.png": img, "caption.txt": f"Order {w} shipped on {h} pallets.\n".encode()},
             {"photo.png": img, "caption.txt": f"Order {w + 1} shipped on {h} pallets.\n".encode()})
        sec = 1.0 + 0.5 * i
        snd = wav(sec, 42100 + i)
        twin("sound_transcript", ["Does the recording say what the transcript says?", "Is the transcript of this "
                                  "recording right?", "Does the transcript match the recording?"][i % 3],
             {"talk.wav": snd, "transcript.txt": f"Recording, {sec} seconds. The speaker says: hello world.\n".encode()},
             {"talk.wav": snd, "transcript.txt": f"Recording, {sec} seconds. The speaker says: goodbye moon.\n".encode()})
        twin("sound_description", ["Does the description match the recording?", "Is this a description of the sound?",
                                   "Does the label describe what you hear?"][i % 3],
             {"clip.wav": snd, "label.txt": f"A {sec} second recording of a dog barking {n} times.\n".encode()},
             {"clip.wav": snd, "label.txt": f"A {sec} second recording of a cat meowing {n} times.\n".encode()})
        # measurable kinds: answered, and right
        twin("image_size_caption", ["Is the image the size the caption states?", "Does the picture have the "
                                    "dimensions the caption gives?", "Is the photo as wide and tall as the note says?"][i % 3],
             {"photo.png": img, "caption.txt": f"A {w} x {h} pixel photo.\n".encode()},
             {"photo.png": img, "caption.txt": f"A {w + 10} x {h} pixel photo.\n".encode()})
        twin("sound_duration_label", ["Is the recording as long as the label says?", "Does the clip last as long as "
                                      "stated?", "Is the duration in the notes right?"][i % 3],
             {"clip.wav": snd, "label.txt": f"The clip lasts {sec} seconds.\n".encode()},
             {"clip.wav": snd, "label.txt": f"The clip lasts {sec + 1.5} seconds.\n".encode()})
        vals = [round(10 + 7.25 * k + i, 2) for k in range(5)]
        part = round(sum(vals[:2]), 2)
        tot = round(sum(vals), 2)
        twin("report_subtotal_and_total", "Does the total in the report match the amounts in the spreadsheet?",
             {"report.md": f"# Costs\n\nHardware subtotal: {part:.2f}.\n\nTotal: {tot:.2f}.\n".encode(),
              "costs.csv": csv_amounts(vals)},
             {"report.md": f"# Costs\n\nHardware subtotal: {part:.2f}.\n\nTotal: {tot + 12:.2f}.\n".encode(),
              "costs.csv": csv_amounts(vals)})
        twin("report_total_with_date", "Does the total in the report match the amounts in the spreadsheet?",
             {"report.md": f"# Costs, 12.03.2024\n\nPaid on 2024-03-{10 + i}. Total: {tot:.2f}.\n".encode(),
              "costs.csv": csv_amounts(vals)},
             {"report.md": f"# Costs, 12.03.2024\n\nPaid on 2024-03-{10 + i}. Total: {tot + 3:.2f}.\n".encode(),
              "costs.csv": csv_amounts(vals)})
        grams = [round(200 + 37.5 * k + 10 * i, 1) for k in range(4)]
        kg = sum(grams) / 1000
        gcsv = ("item,weight_g\n" + "".join(f"part {k},{g}\n" for k, g in enumerate(grams))).encode()
        twin("report_total_other_unit", "Does the total weight in the report match the weights in the table?",
             {"report.md": f"# Parts\n\nTotal weight: {kg:.4f} kg.\n".encode(), "parts.csv": gcsv},
             {"report.md": f"# Parts\n\nTotal weight: {kg + 0.25:.4f} kg.\n".encode(), "parts.csv": gcsv})
        hours = [1.5 + k + i for k in range(4)]
        cost = [round(40.25 * (k + 1) + i, 2) for k in range(4)]
        hc = ("task,hours,cost\n" + "".join(f"t{k},{h},{c:.2f}\n" for k, (h, c) in enumerate(zip(hours, cost)))).encode()
        twin("report_two_totals", "Does the total cost in the report match the spreadsheet?",
             {"report.md": f"# Work\n\nTotal hours: {sum(hours):g}.\n\nTotal cost: {sum(cost):.2f}.\n".encode(), "work.csv": hc},
             {"report.md": f"# Work\n\nTotal hours: {sum(hours):g}.\n\nTotal cost: {sum(cost) + 40:.2f}.\n".encode(), "work.csv": hc})
        files = {f"f{k}.txt": b"x\n" for k in range(n + 2)}
        zb = io.BytesIO()
        with zipfile.ZipFile(zb, "w") as z:
            for name, body in files.items():
                z.writestr(name, body)
        twin("archive_manifest_count", "Does the archive hold as many files as the manifest says?",
             {"bundle.zip": zb.getvalue(), "manifest.txt": f"The archive holds {n + 2} files.\n".encode()},
             {"bundle.zip": zb.getvalue(), "manifest.txt": f"The archive holds {n + 4} files.\n".encode()})
    return items


def main(out):
    resource.setrlimit(resource.RLIMIT_AS, (3 << 30, 3 << 30))
    from neuroscifi.acceptance.omni.subject import relate
    per, wrong = {}, []
    for kind, files, q, truth in build():
        r = relate(files, q)
        a = r["answer"]
        o = "cannot" if a is None else "right" if a == truth else "wrong"
        c = per.setdefault(kind, {"right": 0, "cannot": 0, "wrong": 0})
        c[o] += 1
        if o == "wrong":
            wrong.append({"kind": kind, "q": q, "truth": truth, "because": r["because"][:2]})
    tot = {k: sum(c[k] for c in per.values()) for k in ("right", "cannot", "wrong")}
    json.dump({"total": tot, "per_kind": per, "wrong": wrong}, open(out, "w"), indent=1)
    print(json.dumps(tot))


if __name__ == "__main__":
    main(sys.argv[1])
