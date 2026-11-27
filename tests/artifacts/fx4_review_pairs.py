"""Lane FX4: the security reviewer's relater constructions (review3, H1-H3, M1), rebuilt here from the review's
DESCRIPTION with this module's own builders, names and values (no file of the review is imported). Each pair is
(name, files, question, truth); truth None means only "cannot" is right. Used by tests/artifacts/test_fx4_relate.py
and by scripts/omnimind_fx3/corpus_relate.py's evidence."""
from __future__ import annotations

import hashlib
import io
import math
import struct
import zipfile
import zlib

import numpy as np

RATE = 8000


def zipped(files: dict, method=zipfile.ZIP_DEFLATED) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", method) as z:
        for name, body in files.items():
            z.writestr(zipfile.ZipInfo(name, (2023, 6, 7, 8, 9, 10)), body)
    return buf.getvalue()


def png(w: int, h: int) -> bytes:
    raw = b"".join(b"\x00" + bytes((7 * x + 3 * y) % 251 for x in range(w)) for y in range(h))

    def chunk(kind, body):
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 0, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def tones(onsets, *, freq=600.0, length=0.2) -> bytes:
    total = int((max(onsets) + 1.2) * RATE)
    x = np.zeros(total)
    n = int(length * RATE)
    t = np.arange(n) / RATE
    for start in onsets:
        a = int(round(start * RATE))
        x[a:a + n] += 0.5 * np.sin(2 * math.pi * freq * t)
    pcm = (np.clip(x, -1, 1) * 32767).astype("<i2").tobytes()
    fmt = struct.pack("<HHIIHH", 1, 1, RATE, RATE * 2, 2, 16)
    body = b"WAVE" + b"fmt " + struct.pack("<I", 16) + fmt + b"data" + struct.pack("<I", len(pcm)) + pcm
    return b"RIFF" + struct.pack("<I", len(body)) + body


def nodes_svg(labels, edges, style="") -> bytes:
    pos = {lab: (50 + 100 * i, 60 + 80 * (i % 2)) for i, lab in enumerate(labels)}
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{100 + 100 * len(labels)}" height="220">']
    for k, (a, b) in enumerate(edges):
        (x1, y1), (x2, y2) = pos[a], pos[b]
        extra = style if k == len(edges) - 1 else ""
        out.append(f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="black" {extra}/>')
    for lab, (x, y) in pos.items():
        out.append(f'<circle cx="{x}" cy="{y}" r="20" fill="white" stroke="black"/>'
                   f'<text x="{x}" y="{y + 4}" text-anchor="middle">{lab}</text>')
    out.append("</svg>")
    return "".join(out).encode()


PACK = {"tools/run": bytes(range(100, 230)) * 3, "docs/info.md": b"see the guide\n" * 6,
        "tables/stock.csv": b"k,v\n3,4\n" * 30}


def size_lines(files: dict, override: dict | None = None) -> str:
    return "".join(f"{(override or {}).get(n, len(b)):>6}  {n}\n" for n, b in files.items())


Q_SIZES = "Do the byte sizes in the list match the files in the archive?"
PEOPLE = ["Lin", "Omar", "Sade", "Ivo"]
TIES = [("Lin", "Omar"), ("Sade", "Omar"), ("Ivo", "Sade")]


def pairs() -> list:
    out = []
    add = out.append
    total = sum(len(b) for b in PACK.values())
    # H3: a footer total or a count line in an honest manifest is not a member
    add(("footer_total", {"p.zip": zipped(PACK), "list.txt": (size_lines(PACK) + f"sum  {total}\n").encode()},
         Q_SIZES, True))
    add(("count_line", {"p.zip": zipped(PACK), "list.txt": ("Entries: 3\n" + size_lines(PACK)).encode()},
         Q_SIZES, True))
    # H1: a count question is not decided by sizes
    wrong_size = size_lines(PACK, {"tables/stock.csv": 1234})
    add(("count_question", {"p.zip": zipped(PACK), "list.txt": wrong_size.encode()},
         "Is the number of entries in the list the number of files in the archive?", True))
    add(("count_question_as_many", {"p.zip": zipped(PACK), "list.txt": wrong_size.encode()},
         "Does the list hold as many files as the archive?", True))
    # H2: hidden text (Markdown comment, RTF hidden group) never decides
    visible = "# Packing list\n\n" + "".join(f"- {n}: {1234 if n == 'tables/stock.csv' else len(b)} bytes\n"
                                             for n, b in PACK.items())
    add(("md_comment_sizes", {"p.zip": zipped(PACK), "list.md": (visible + "\n<!--\n" + size_lines(PACK)
                                                                  + "-->\n").encode()}, Q_SIZES, False))
    rtf = ("{\\rtf1\\ansi{\\fonttbl{\\f0 Arial;}}\n" + "".join(f"{n} is {1234 if n == 'tables/stock.csv' else len(b)}"
                                                               f" bytes\\par\n" for n, b in PACK.items())
           + "{\\v\n" + size_lines(PACK) + "}\n}\n")
    add(("rtf_hidden_sizes", {"p.zip": zipped(PACK), "list.rtf": rtf.encode()}, Q_SIZES, False))
    sums = "".join(f"{hashlib.sha256(b).hexdigest()}  {n}\n" for n, b in PACK.items())
    add(("md_comment_sums", {"p.zip": zipped(PACK), "SUMS.md": ("# Sums\n\nThese digests are out of date.\n\n<!--\n"
                                                                 + sums + "-->\n").encode()},
         "Do the checksums of the archive's files match?", False))
    # H3: link lines that are not relations
    add(("link_unrelated", {"o.svg": nodes_svg(PEOPLE, TIES),
                            "o.txt": b"Lin reports to Omar.\nSade has nothing to do with Omar.\nIvo reports to Sade.\n"},
         "Does the text give the drawing's connections correctly?", False))
    add(("link_formerly", {"o.svg": nodes_svg(PEOPLE, TIES),
                           "o.txt": b"Lin reports to Omar.\nSade no longer reports to Omar.\nIvo reports to Sade.\n"},
         "Are the links in the drawing the ones in the text?", False))
    add(("link_invisible", {"o.svg": nodes_svg(PEOPLE, TIES + [("Lin", "Ivo")], 'stroke-opacity="0"'),
                            "o.txt": b"Lin reports to Omar.\nSade reports to Omar.\nIvo reports to Sade.\n"
                                     b"Lin coaches Ivo.\n"},
         "Are the links in the drawing the ones in the text?", False))
    add(("link_prose_memo", {"o.svg": nodes_svg(PEOPLE, TIES),
                             "memo.txt": b"Lin and Omar had lunch.\nSade and Omar sit together.\nIvo and Sade "
                                         b"drafted the plan.\n"}, "Is the memo right about this drawing?", None))
    # H3: whole-second cue times; a sheet that lists ends
    add(("cues_whole_seconds", {"s.wav": tones([0.15, 1.1, 2.05]), "cues.txt": b"0:01 gong\n0:02 gong\n0:03 gong\n"},
         "Do the gongs start at the times in the cue sheet?", False))
    add(("cues_with_ends", {"s.wav": tones([0.4, 2.2], length=0.6),
                            "cues.txt": b"0:00.400 tone starts\n0:01.000 tone stops\n"},
         "Do the onset times in the cue sheet match the sound?", None))
    # H3/R4: depiction questions carrying a size word
    add(("depiction_big", {"p.png": png(70, 40), "c.txt": b"A big green tractor, 70 pixels wide and 40 pixels high.\n"},
         "Does the picture show a big green tractor, as the caption says?", None))
    add(("depiction_wide", {"p.png": png(70, 40), "c.txt": b"A dog on a wide bench; the image is 70 pixels wide.\n"},
         "Does the image really show a dog on a wide bench?", None))
    # a count question about drawn shapes answered by another count
    shapes = ('<svg xmlns="http://www.w3.org/2000/svg" width="300" height="90">'
              + "".join(f'<rect x="{8 + 36 * i}" y="8" width="26" height="26"/>' for i in range(2))
              + "".join(f'<circle cx="{120 + 36 * i}" cy="50" r="10"/>' for i in range(5)) + "</svg>").encode()
    add(("count_of_squares", {"d.svg": shapes, "cap.txt": b"The figure holds 7 shapes, 5 of them squares.\n"},
         "Is the number of squares right?", False))
    # R4: per-item counts are not the column total
    add(("per_item_each", {"laps.csv": b"Rower,Lengths\nA,25\nB,25\nC,25\n",
                           "note.txt": b"Each rower completed 25 lengths.\n"}, "Is the season total right?", True))
    add(("per_item_every", {"laps.csv": b"Day,Lengths\n1,12\n2,12\n3,12\n4,12\n",
                            "note.txt": b"Every day the club swam 12 lengths.\n"}, "Is the total right?", True))
    # R4: a drawing declared in millimetres is not a picture of pixels
    mm = (b'<svg xmlns="http://www.w3.org/2000/svg" width="80mm" height="30mm" viewBox="0 0 80 30">'
          b'<rect x="2" y="2" width="6" height="6"/></svg>')
    add(("svg_millimetres", {"d.svg": mm, "cap.txt": b"The drawing is 80 pixels wide and 30 pixels high.\n"},
         "Is the drawing as wide as the caption says?", None))
    # a ZIP whose headers lie about a member's size (refused as a whole)
    lie = bytearray(zipped({"tables/stock.csv": PACK["tables/stock.csv"]}, zipfile.ZIP_STORED))
    central = lie.find(b"PK\x01\x02")
    struct.pack_into("<I", lie, central + 24, 777)
    struct.pack_into("<I", lie, 22, 777)
    add(("zip_lying_size", {"p.zip": bytes(lie), "m.txt": b"   777  tables/stock.csv\n"}, Q_SIZES, False))
    # a question that names one aggregate keeps the agreeing answer
    add(("named_max_unread_total", {"c.csv": b"Part,Cost (GBP)\nBolts,90\nNuts,15\nGlue,8\n",
                                    "memo.txt": b"The largest cost was 90 GBP.\nAltogether we paid 300 GBP.\n"},
         "Is the largest cost in the memo right?", True))
    # the thing named is not measured by any comparison
    add(("not_measured_direction", {"o.svg": nodes_svg(PEOPLE, TIES),
                                    "o.txt": b"Omar reports to Lin.\nOmar reports to Sade.\nSade reports to Ivo.\n"},
         "Does the drawing show who reports to whom, as the text says?", None))
    add(("not_measured_pitch", {"s.wav": tones([0.6, 1.6, 2.6], freq=440.0),
                                "cues.txt": b"0:00.600 high C\n0:01.600 high C\n0:02.600 high C\n"},
         "Do the tones at those cue times have the pitch the sheet names?", None))
    add(("not_measured_dates", {"p.zip": zipped(PACK), "list.txt": size_lines(PACK).encode()},
         "Were the files in the archive modified on the dates the list gives?", None))
    add(("not_measured_content", {"p.zip": zipped(PACK), "list.txt": size_lines(PACK).encode()},
         "Does the archive's CSV file have the content the list says?", None))
    add(("not_measured_distance", {"m.svg": nodes_svg(PEOPLE, TIES),
                                   "d.txt": b"Lin to Omar: 4 km\nSade to Omar: 6 km\nIvo to Sade: 3 km\n"},
         "Are the connection lengths in the drawing the distances in the text?", None))
    # M1: a listing cut off by the reading bound, or padded past it, never answers True
    many = {f"x/{i:05d}.dat": b"z%d\n" % i for i in range(3600)}
    lines = [f"{hashlib.sha256(b).hexdigest()}  {n}\n" for n, b in many.items()]
    lines[-1] = "f" * 64 + lines[-1][64:]
    add(("listing_spoiled_tail", {"p.zip": zipped(many), "SUMS": "".join(lines).encode()},
         "Do the checksums of the archive's files match?", False))
    two = {"a.bin": b"a" * 11, "b.bin": b"b" * 21}
    add(("listing_padded", {"p.zip": zipped(two), "SUMS": (f"{hashlib.sha256(two['a.bin']).hexdigest()}  a.bin\n"
                                                           + "\n" * (300 << 10) + "0" * 64 + "  b.bin\n").encode()},
         "Do the checksums of the archive's files match?", False))
    return out
