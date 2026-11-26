"""Family `compound`: artifacts made of artifacts, where the question needs the right member.

- a zip holding a CSV, a PNG chart and a Markdown report whose figure and text refer to both;
- a DOCX with an embedded image, its caption and a table (the image is a member of the DOCX's own zip);
- an XLSX with three or four sheets (the question names the sheet);
- an archive in an archive (a tar.gz inside a zip, a zip inside a tar.gz);
- a DOCX, a notebook or a WAV inside a zip.

Questions name members with `::` (DESIGN §2 Bundle/Member paths; a sheet's name for a workbook).
"""
from __future__ import annotations

from . import Task, check_split, default_score, enc
from .cases import COMPOUND
from .intake import (REL, csv_bytes, doc_content, docx_bytes, gen_ipynb, luminance, pixels, q, rng_for, sentence,
                     serial_to_epoch, table_values, tone_bursts, word, workbook_values, xlsx_bytes)


def _report_zip(rng, case):
    t = table_values(rng, {"columns": case["columns"], "rows": case["rows"]})
    w, h = case["chart_size"]
    arr = pixels(rng, h, w, 1, 255)
    chart = enc.png(arr, mode="gray")
    heads = [f"{word(rng).capitalize()} summary", "Method", "Result"]
    caption = f"Figure 1: {word(rng)} by {word(rng)}"
    md = (f"# {heads[0]}\n\nThe values come from [the table](data/{case['csv']}).\n\n## {heads[1]}\n\n"
          f"{sentence(rng)}\n\n## {heads[2]}\n\n![{caption}](figures/{case['png']})\n").encode()
    files = {f"data/{case['csv']}": csv_bytes(t), f"figures/{case['png']}": chart, "report.md": md}
    n = case["rows"]
    x, y = int(rng.integers(w)), int(rng.integers(h))
    return files, [
        q("bundle.members", sorted(files)),
        q(f"data/{case['csv']}::table.column_mean:{t['names'][2]}", sum(t["cols"][2]) / n, REL),
        q(f"data/{case['csv']}::table.n_rows", n),
        q(f"figures/{case['png']}::image.size", [w, h]),
        q(f"figures/{case['png']}::image.pixel:{x},{y}", [int(arr[y, x])]),
        q(f"figures/{case['png']}::image.mean_gray", luminance(arr, 255), {"abs": 1e-6}),
        q("report.md::doc.headings", heads),
        q("report.md::doc.figures", [caption]),
        q("report.md::doc.figure_members", [f"figures/{case['png']}"])]


def build(case) -> tuple[dict, dict, list]:
    kind, rng, name = case["make"], rng_for(case["seed"]), case["file"]
    if kind == "report_zip":
        files, qs = _report_zip(rng, case)
        return {name: enc.zip_files(files)}, {name: "application/zip"}, qs
    if kind == "docx_image":
        d = doc_content(rng, {"sections": case["sections"], "rows": case["rows"], "figures": 1})
        w, h = case["image_size"]
        arr = pixels(rng, h, w, 3, 255)
        header, rows = d["table"]
        r = int(rng.integers(len(rows)))
        data = docx_bytes(d, image=enc.png(arr, mode="rgb"))
        return ({name: data}, {name: "application/vnd.openxmlformats-officedocument.wordprocessingml.document"},
                [q("doc.headings", [s[1] for s in d["sections"]]), q(f"doc.table_cell:0,{r},0", rows[r][0]),
                 q(f"doc.table_cell:0,{r},1", rows[r][1], REL), q("doc.figures", d["figures"]),
                 q("word/media/image1.png::image.size", [w, h]),
                 q("word/media/image1.png::image.pixel:0,0", [int(v) for v in arr[0, 0]])])
    if kind == "xlsx_sheets":
        sheets = workbook_values(rng, case)
        qs = [q("table.sheets", [s for s, _, _ in sheets])]
        for sheet, header, rows in sheets:
            r = int(rng.integers(len(rows)))
            qs += [q(f"{sheet}::table.n_rows", len(rows)), q(f"{sheet}::table.cell:{r},1", rows[r][1], REL),
                   q(f"{sheet}::table.cell:{r},2", serial_to_epoch(rows[r][2][1]), {"abs": 1.0})]
        return ({name: xlsx_bytes(sheets)}, {name: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"},
                qs)
    if kind in ("tgz_in_zip", "zip_in_tgz"):
        t = table_values(rng, {"columns": case["columns"], "rows": case["rows"]})
        owner = f"{word(rng)} {word(rng)}"
        inner_files = {"data/values.csv": csv_bytes(t), "meta.json": enc.jdump({"owner": owner})}
        if kind == "tgz_in_zip":
            inner_name = "inner.tar.gz"
            inner = enc.gzip_member(enc.tar_archive(inner_files))
            outer = enc.zip_files({inner_name: inner, "readme.txt": b"see inner\n"})
            declared = "application/zip"
        else:
            inner_name = "inner.zip"
            inner = enc.zip_files(inner_files)
            outer = enc.gzip_member(enc.tar_archive({inner_name: inner, "readme.txt": b"see inner\n"}))
            declared = "application/gzip"
        n = case["rows"]
        return ({name: outer}, {name: declared},
                [q(f"{inner_name}::data/values.csv::table.n_rows", n),
                 q(f"{inner_name}::data/values.csv::table.column_mean:{t['names'][2]}", sum(t["cols"][2]) / n, REL),
                 q(f"{inner_name}::meta.json::tree.value:owner", owner),
                 q(f"{inner_name}::bundle.members", sorted(inner_files))])
    if kind == "docx_in_zip":
        d = doc_content(rng, {"sections": 3, "rows": 3, "figures": 0})
        files = {"memo/memo.docx": docx_bytes(d), "memo/notes.txt": (sentence(rng) + "\n").encode()}
        return ({name: enc.zip_files(files)}, {name: "application/zip"},
                [q("memo/memo.docx::doc.headings", [s[1] for s in d["sections"]]),
                 q("memo/memo.docx::doc.title", d["title"]), q("bundle.members", sorted(files))])
    if kind == "notebook_in_zip":
        nb_files, _, nb_qs = gen_ipynb({"cells": case["cells"], "file": "work/analysis.ipynb"}, rng)
        t = table_values(rng, {"columns": ("k", "v", "n", "t"), "rows": 4})
        files = dict(nb_files)
        files["work/input.csv"] = csv_bytes(t)
        qs = [(f"work/analysis.ipynb::{qq}", e, tol, kd) for qq, e, tol, kd in nb_qs if not qq.startswith("detect.")]
        return {name: enc.zip_files(files)}, {name: "application/zip"}, qs + [q("work/input.csv::table.n_rows", 4)]
    if kind == "wav_in_zip":
        rate, freq = case["rate"], case["freq"]
        x = tone_bursts(rate, freq, case["bursts"], 0.2, 0.2, 0.1)
        meta = {"recorded_by": word(rng), "tone_hz": freq}
        files = {"audio/take.wav": enc.wav(x, rate), "audio/meta.json": enc.jdump(meta)}
        return ({name: enc.zip_files(files)}, {name: "application/zip"},
                [q("audio/take.wav::audio.dominant_hz", freq, {"rel": 0.01}),
                 q("audio/take.wav::audio.n_onsets", case["bursts"]),
                 q("audio/take.wav::audio.duration_s", len(x) / rate, {"abs": 1e-6}),
                 q("audio/meta.json::tree.value:recorded_by", meta["recorded_by"])])
    raise ValueError(kind)


def tasks(split: str) -> list[Task]:
    check_split(split)
    out = []
    for case in COMPOUND[split]:
        files, declared, questions = build(case)
        for question, expect, tolerance, kind in questions:
            out.append(Task(f"compound/{split}/{case['name']}/{question}", "compound", files, declared, question,
                            expect, tolerance, kind))
    return out


def score(subject, split: str) -> dict:
    return default_score("compound", tasks, subject, split)

