"""Lane R's own pairs of artifacts, built here by hand with the standard library (struct, zlib, wave, zipfile, json):
kinds of pairing that the cross family's public splits do not have, each as a matching and a non-matching twin that
differ in one value, plus unrelated pairs whose right answer is "cannot". The truth is known by construction.

Nothing here imports the benchmark (acceptance/omni) or the product's generators: the files are written from the
formats themselves, so that relate.py is examined on bytes it was not shaped around.
"""
from __future__ import annotations

import io
import json
import math
import struct
import wave
import zipfile
import zlib


# ---- bytes ---------------------------------------------------------------------------------------------------------

def png(width: int, height: int, gray: int = 128) -> bytes:
    def chunk(kind, body):
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)
    raw = b"".join(b"\x00" + bytes([gray]) * width for _ in range(height))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def wav_tones(freqs, tone_s=0.2, gap_s=0.05, rate=8000) -> bytes:
    frames = bytearray(b"\x00\x00" * int(0.05 * rate))
    n = int(tone_s * rate)
    for f in freqs:
        for i in range(n):
            env = min(1.0, i / 40, (n - 1 - i) / 40)
            frames += struct.pack("<h", int(0.5 * 32767 * env * math.sin(2 * math.pi * f * i / rate)))
        frames += b"\x00\x00" * int(gap_s * rate)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(bytes(frames))
    return buf.getvalue()


def wav_seconds(seconds: float, rate=8000) -> bytes:
    n = int(round(seconds * rate))
    frames = b"".join(struct.pack("<h", int(8000 * math.sin(2 * math.pi * 220 * i / rate))) for i in range(n))
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(frames)
    return buf.getvalue()


def zip_of(names) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for n in names:
            info = zipfile.ZipInfo(n, date_time=(1980, 1, 1, 0, 0, 0))
            z.writestr(info, f"contents of {n}\n")
    return buf.getvalue()


def midi(notes, ticks=480) -> bytes:
    """Format 0, one track, default tempo (120 bpm): each note a quarter note, back to back."""
    def var(n):
        out = [n & 0x7F]
        n >>= 7
        while n:
            out.insert(0, (n & 0x7F) | 0x80)
            n >>= 7
        return bytes(out)
    track = b""
    for p in notes:
        track += var(0) + bytes([0x90, p, 90]) + var(ticks) + bytes([0x80, p, 0])
    track += var(0) + b"\xff\x2f\x00"
    return (b"MThd" + struct.pack(">IHHH", 6, 0, 1, ticks) + b"MTrk" + struct.pack(">I", len(track)) + track)


def obj_box(w, d, h, unit_comment="millimetres") -> bytes:
    v = [(x, y, z) for z in (0, h) for y in (0, d) for x in (0, w)]
    f = [(1, 3, 2), (2, 3, 4), (5, 6, 8), (5, 8, 7), (1, 2, 6), (1, 6, 5), (3, 7, 8), (3, 8, 4),
         (1, 5, 7), (1, 7, 3), (2, 4, 8), (2, 8, 6)]
    lines = ([f"# units: {unit_comment}"] if unit_comment else []) + ["o box"]
    lines += [f"v {x} {y} {z}" for x, y, z in v] + [f"f {a} {b} {c}" for a, b, c in f]
    return ("\n".join(lines) + "\n").encode()


def gpx(minutes: int, points: int = 10) -> bytes:
    pts = []
    for i in range(points):
        t = i * minutes * 60 // (points - 1)
        hh, mm, ss = 9 + t // 3600, t // 60 % 60, t % 60
        pts.append(f'<trkpt lat="{51.5 + i * 0.001:.4f}" lon="{-0.12 + i * 0.001:.4f}">'
                   f"<time>2026-05-02T{hh:02d}:{mm:02d}:{ss:02d}Z</time></trkpt>")
    return ('<?xml version="1.0"?><gpx version="1.1" creator="test" xmlns="http://www.topografix.com/GPX/1/1">'
            f'<trk><name>walk</name><trkseg>{"".join(pts)}</trkseg></trk></gpx>').encode()


def svg_bars(labels, values, name, *, ymax) -> bytes:
    """A bar chart written from the reading contract (DESIGN section 6) by hand."""
    left, right, top, bottom = 60.0, 560.0, 20.0, 320.0
    band = (right - left) / len(labels)
    step = ymax / 5

    def py(v):
        return bottom - v / ymax * (bottom - top)
    xt = "".join(f'<g class="tick" transform="translate({left + (i + .5) * band:.2f},{bottom})"><text>{c}</text></g>'
                 for i, c in enumerate(labels))
    yt = "".join(f'<g class="tick" transform="translate({left},{py(k * step):.2f})"><text>{k * step:g}</text></g>'
                 for k in range(6))
    marks = "".join(f'<rect class="mark" x="{left + i * band + band * .15:.2f}" y="{py(v):.2f}" '
                    f'width="{band * .7:.2f}" height="{bottom - py(v):.2f}"/>' for i, v in enumerate(values))
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 600 360"><title>{name}</title>'
            f'<g class="axis x">{xt}</g><g class="axis y">{yt}</g>'
            f'<g class="plot"><g class="series" data-name="{name}">{marks}</g></g></svg>').encode()


def csv(header, rows) -> bytes:
    return (",".join(header) + "\n" + "".join(",".join(str(c) for c in r) + "\n" for r in rows)).encode()


# ---- the pairs -----------------------------------------------------------------------------------------------------

TEMPS = [18.5, 21.0, 23.25, 19.75, 24.5, 22.0]          # mean 21.5
VISITS = [("Jan", 120), ("Feb", 95), ("Mar", 140), ("Apr", 110)]


def pairs():
    """(kind, question, files of the matching twin, files of the non-matching twin)."""
    out = []
    t2 = list(TEMPS)
    t2[2] = 26.25
    out.append(("doc_mean_csv", "Does the note's average temperature agree with the readings?",
                {"note.md": b"# Week\n\nThe average temperature was 21.5 \xc2\xb0C over the six days.\n",
                 "temps.csv": csv(["Day", "Temp (°C)"], [(f"D{i + 1}", v) for i, v in enumerate(TEMPS)])},
                {"note.md": b"# Week\n\nThe average temperature was 21.5 \xc2\xb0C over the six days.\n",
                 "temps.csv": csv(["Day", "Temp (°C)"], [(f"D{i + 1}", v) for i, v in enumerate(t2)])}))
    out.append(("doc_size_png", "Is the screenshot the size the README says?",
                {"README.md": b"# Screens\n\nThe screenshot is 320 x 200 pixels.\n", "shot.png": png(320, 200)},
                {"README.md": b"# Screens\n\nThe screenshot is 320 x 240 pixels.\n", "shot.png": png(320, 200)}))
    out.append(("doc_duration_wav", "Does the recording last as long as the notes say?",
                {"notes.txt": b"Field notes.\nThe recording lasts 2.5 seconds.\n", "clip.wav": wav_seconds(2.5)},
                {"notes.txt": b"Field notes.\nThe recording lasts 2.5 seconds.\n", "clip.wav": wav_seconds(3.0)}))
    out.append(("doc_count_zip", "Does the archive hold as many files as the letter says?",
                {"letter.txt": b"Dear team,\nThe archive holds 3 files.\nRegards\n", "pack.zip": zip_of("abc")},
                {"letter.txt": b"Dear team,\nThe archive holds 3 files.\nRegards\n", "pack.zip": zip_of("abcd")}))
    code4 = b"def a(x):\n    return b(x)\n\n\ndef b(x):\n    return x\n\n\ndef c(x):\n    return x\n\n\ndef d(x):\n" \
            b"    return x\n"
    out.append(("doc_count_code", "Does the module define the number of functions the README states?",
                {"README.md": b"# tool\n\nThe module defines 4 functions.\n", "tool.py": code4},
                {"README.md": b"# tool\n\nThe module defines 4 functions.\n",
                 "tool.py": code4 + b"\n\ndef e(x):\n    return x\n"}))
    v2 = [v for _, v in VISITS]
    v2[2] = 150
    out.append(("chart_peak_doc", "Does the chart show the peak the summary states?",
                {"summary.md": b"# Visits\n\nVisits peaked at 140 in March.\n",
                 "visits.svg": svg_bars([c for c, _ in VISITS], [v for _, v in VISITS], "Visits", ymax=200)},
                {"summary.md": b"# Visits\n\nVisits peaked at 140 in March.\n",
                 "visits.svg": svg_bars([c for c, _ in VISITS], v2, "Visits", ymax=200)}))
    recs = [{"city": c, "population": p} for c, p in (("Aston", 1200), ("Brill", 860), ("Cray", 4310))]
    recs2 = [dict(r) for r in recs]
    recs2[1]["population"] = 868
    table = csv(["city", "population"], [(r["city"], r["population"]) for r in recs])
    out.append(("json_records_csv", "Do the JSON records hold the same population values as the CSV?",
                {"towns.json": json.dumps(recs).encode(), "towns.csv": table},
                {"towns.json": json.dumps(recs2).encode(), "towns.csv": table}))
    out.append(("midi_wav_count", "Does the recording have one tone per note of the MIDI file?",
                {"tune.mid": midi([60, 62, 64, 65, 67]), "tune.wav": wav_tones([262, 294, 330, 349, 392])},
                {"tune.mid": midi([60, 62, 64, 65, 67]), "tune.wav": wav_tones([262, 294, 330, 349])}))
    out.append(("doc_range_csv", "Do the readings span the range the report gives?",
                {"report.txt": b"Report.\nReadings ranged from 18.5 to 24.5 degrees Celsius.\n",
                 "temps.csv": csv(["Day", "Temp (°C)"], [(f"D{i + 1}", v) for i, v in enumerate(TEMPS)])},
                {"report.txt": b"Report.\nReadings ranged from 18.5 to 24.5 degrees Celsius.\n",
                 "temps.csv": csv(["Day", "Temp (°C)"], [(f"D{i + 1}", v) for i, v in enumerate(t2)])}))
    ev = [("2026-06-01T08:00:00Z", "backup started"), ("2026-06-01T08:20:00Z", "backup copied"),
          ("2026-06-01T08:31:00Z", "backup verified")]
    ev2 = [ev[0], ("2026-06-01T08:40:00Z", "backup copied"), ev[2]]
    log = "".join(f"{t} INFO {m}\n" for t, m in ev).encode()
    out.append(("csv_events_log", "Do the event table and the log agree on the order?",
                {"events.csv": csv(["when", "what"], ev), "ops.log": log},
                {"events.csv": csv(["when", "what"], ev2), "ops.log": log}))
    out.append(("doc_duration_gpx", "Did the walk take as long as the diary says?",
                {"diary.md": b"# Saturday\n\nThe walk took 45 minutes.\n", "walk.gpx": gpx(45)},
                {"diary.md": b"# Saturday\n\nThe walk took 45 minutes.\n", "walk.gpx": gpx(60)}))
    out.append(("doc_volume_obj", "Is the box's volume what the README says?",
                {"README.md": b"# Box\n\nVolume: 24 cm\xc2\xb3.\n", "box.obj": obj_box(20, 30, 40)},
                {"README.md": b"# Box\n\nVolume: 24 cm\xc2\xb3.\n", "box.obj": obj_box(20, 30, 45)}))
    rows = [(f"R{i}", i % 5) for i in range(25)]
    out.append(("doc_count_rows", "Does the survey file hold the number of responses the summary gives?",
                {"summary.txt": b"Summary.\nThe survey has 25 responses.\n",
                 "responses.csv": csv(["id", "score"], rows)},
                {"summary.txt": b"Summary.\nThe survey has 25 responses.\n",
                 "responses.csv": csv(["id", "score"], rows[:24])}))
    load = [43.0, 52.5, 19.0, 76.4, 61.0]
    load2 = list(load)
    load2[3] = 81.2
    out.append(("doc_percent_csv", "Is the peak load in the note the peak in the data?",
                {"note.txt": b"Note.\nPeak load was 76.4%.\n",
                 "load.csv": csv(["Hour", "Load (%)"], [(h, v) for h, v in enumerate(load)])},
                {"note.txt": b"Note.\nPeak load was 76.4%.\n",
                 "load.csv": csv(["Hour", "Load (%)"], [(h, v) for h, v in enumerate(load2)])}))
    return out


def unrelated():
    """(question, files): pairs about which nothing measured in one is stated in the other: "cannot" is right."""
    return [
        ("Do these agree?", {"shot.png": png(64, 48), "clip.wav": wav_seconds(1.0)}),
        ("Does the note match the table?",
         {"note.txt": b"The office opened in 2019 and moved twice.\n",
          "temps.csv": csv(["Day", "Temp"], [(f"D{i}", v) for i, v in enumerate(TEMPS)])}),
        ("Is the mesh consistent with the log?",
         {"box.obj": obj_box(1, 2, 3), "ops.log": b"2026-06-01T08:00:00Z INFO started\n2026-06-01T08:01:00Z INFO "
                                                   b"stopped\n"}),
        ("Does the README describe this picture?",
         {"README.md": b"# Tool\n\nRun it with two arguments. It needs Python 3.12.\n", "p.png": png(10, 10)}),
        ("Does the total match?",
         {"note.txt": b"Costs this year were 12,500 overall, we think.\n",
          "a.csv": csv(["Item", "Cost", "Tax"], [("x", 100, 20), ("y", 200, 40)])}),
        ("Does the chart agree with the table?",
         {"visits.svg": svg_bars(["Jan", "Feb"], [10, 20], "Visits", ymax=50),
          "other.csv": csv(["Name", "Height", "Weight"], [("a", 1.7, 60), ("b", 1.8, 75)])}),
    ]


__all__ = ["csv", "gpx", "midi", "obj_box", "pairs", "png", "svg_bars", "unrelated", "wav_seconds", "wav_tones",
           "zip_of"]
