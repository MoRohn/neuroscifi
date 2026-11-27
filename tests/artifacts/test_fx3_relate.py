"""Lane FX3 (ADR-081), relater classes X-A to X-I found on the spent withheld cross cases. Every pair is built here
(hand-assembled bytes, or the cross family's public generators with seeds of this file's own range 30000-30099, none
of them a benchmark case), as twins whose truth is known by construction. Each class test fails before the lane's
rules; the traps beside them (what must stay "cannot") hold before and after: no answer returns by guessing."""
from __future__ import annotations

import hashlib
import io
import json
import math
import random
import struct
import tarfile
import zipfile
import zlib

import numpy as np
import pytest

from neuroscifi.acceptance.omni import cross
from neuroscifi.acceptance.omni.subject import relate


def _ask(files: dict, question: str):
    return relate(files, question)["answer"]


# ---- X-C: an archive against a listing of its members ---------------------------------------------------------------

MEMBERS = {"bin/run": bytes(range(200)) * 3, "doc/guide.txt": b"read me first\n" * 9, "data/a.dat": b"\x07" * 333}


def _zip(files: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, body in files.items():
            z.writestr(zipfile.ZipInfo(name, (2022, 2, 2, 2, 2, 2)), body)
    return buf.getvalue()


def _tar(files: dict) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.USTAR_FORMAT) as t:
        for name, body in files.items():
            info = tarfile.TarInfo(name)
            info.size, info.mtime = len(body), 0
            t.addfile(info, io.BytesIO(body))
    return buf.getvalue()


def _sums(files: dict, algo: str = "sha256", spoil: str | None = None) -> bytes:
    lines = []
    for name, body in files.items():
        digest = hashlib.new(algo, body).hexdigest()
        if name == spoil:
            digest = ("0" if digest[0] != "0" else "1") + digest[1:]
        lines.append(f"{digest}  {name}\n")
    return "".join(lines).encode()


@pytest.mark.parametrize("algo", ["sha256", "md5", "sha1"])
@pytest.mark.parametrize("question", ["Do the files in the bundle match the checksum list?",
                                      "Are the hashes in this list the hashes of the archive's files?"])
def test_a_checksum_listing_is_compared_member_by_member(algo, question):
    zipped = _zip(MEMBERS)
    assert _ask({"bundle.zip": zipped, "SUMS": _sums(MEMBERS, algo)}, question) is True
    assert _ask({"bundle.zip": zipped, "SUMS": _sums(MEMBERS, algo, spoil="doc/guide.txt")}, question) is False
    changed = dict(MEMBERS, **{"data/a.dat": b"\x07" * 332 + b"\x08"})
    assert _ask({"bundle.zip": _zip(changed), "SUMS": _sums(MEMBERS, algo)}, question) is False
    # lane FX5 (C1): a question with a word the comparison's grammar does not read ("made") is "cannot"
    assert _ask({"bundle.zip": zipped, "SUMS": _sums(MEMBERS, algo)},
                "Was this list of hashes made from this archive?") is None


def test_a_size_listing_in_plain_text_is_compared_member_by_member():
    zipped = _zip(MEMBERS)
    text = "".join(f"{len(b):>7}  {n}\n" for n, b in MEMBERS.items()).encode()
    as_json = json.dumps({"entries": [{"name": n, "size": len(b)} for n, b in MEMBERS.items()]}).encode()
    q = "Are the sizes in the manifest the sizes of the files in the archive?"
    assert _ask({"pack.zip": zipped, "manifest.txt": text}, q) is True
    # lane FX5 (C2): a listing is read from plain text only; a JSON listing is "cannot"
    assert _ask({"pack.zip": zipped, "manifest.json": as_json}, q) is None
    wrong = text.replace(b"    333", b"    334")
    assert _ask({"pack.zip": zipped, "manifest.txt": wrong}, q) is False
    # sizes are measured for a TAR archive too
    assert _ask({"pack.tar": _tar(MEMBERS), "manifest.txt": text}, q) is True


def test_listing_traps_stay_cannot():
    zipped = _zip(MEMBERS)
    q = "Do the files in the bundle match the checksum list?"
    # a listing of another archive's files names none of these members: not a listing of this archive
    other = _sums({"x/one.bin": b"1", "x/two.bin": b"2", "x/three.bin": b"3"})
    assert _ask({"bundle.zip": zipped, "SUMS": other}, q) is None
    # a TAR's members are not read again to hash them: a digest listing of a TAR cannot be checked
    assert _ask({"bundle.tar": _tar(MEMBERS), "SUMS": _sums(MEMBERS)}, q) is None


def test_a_listing_that_names_a_member_the_archive_lacks_disagrees():
    zipped = _zip(MEMBERS)
    q = "Do the files in the bundle match the checksum list?"
    # lane FX5 (C3): an extra listed member alone is a difference on one side only ("does the list give the digests
    # of all the files" holds): "cannot"; a member renamed (listed and missing on both sides) disagrees
    listing = _sums(MEMBERS) + _sums({"doc/missing.txt": b"gone"})
    assert _ask({"bundle.zip": zipped, "SUMS": listing}, q) is None
    renamed = _sums(MEMBERS).replace(b"doc/guide.txt", b"doc/other.txt")
    assert _ask({"bundle.zip": zipped, "SUMS": renamed}, q) is False


# ---- X-D: drawn links against written links ------------------------------------------------------------------------

LABELS = ("Ash", "Birch", "Cedar", "Douglas", "Elm")


def _graph_svg(edges) -> bytes:
    pos = {lab: (40 + 90 * i, 60 + 70 * (i % 2)) for i, lab in enumerate(LABELS)}
    parts = ['<svg xmlns="http://www.w3.org/2000/svg" width="500" height="220">']
    for a, b in edges:
        (x1, y1), (x2, y2) = pos[a], pos[b]
        parts.append(f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="black"/>')
    for lab, (x, y) in pos.items():
        parts.append(f'<rect x="{x - 20}" y="{y - 12}" width="40" height="24" fill="white" stroke="black"/>'
                     f'<text x="{x}" y="{y + 4}" text-anchor="middle">{lab}</text>')
    parts.append("</svg>")
    return "".join(parts).encode()


EDGES = [("Ash", "Birch"), ("Birch", "Cedar"), ("Cedar", "Douglas"), ("Douglas", "Elm")]


def _edge_text(edges, verb="hands over to") -> bytes:
    return "".join(f"* {a} {verb} {b}\n" for a, b in edges).encode()


@pytest.mark.parametrize("question", ["Do the connections in the drawing match the notes?",
                                      "Are the connections in the picture the ones written in the notes?"])
def test_drawn_links_are_compared_with_the_links_a_text_names(question):
    svg = _graph_svg(EDGES)
    assert _ask({"flow.svg": svg, "notes.txt": _edge_text(EDGES)}, question) is True
    moved = EDGES[:2] + [("Cedar", "Elm")] + EDGES[3:]
    assert _ask({"flow.svg": svg, "notes.txt": _edge_text(moved)}, question) is False
    assert _ask({"flow.svg": _graph_svg(moved), "notes.txt": _edge_text(EDGES)}, question) is False


def test_link_traps_stay_cannot():
    svg = _graph_svg(EDGES)
    # lane FX5 (C1): words the link grammar does not read ("hand-over", "down") leave the question unread
    for question in ("Do the connections in the drawing match the hand-over notes?",
                     "Are the connections in the picture the ones written down?"):
        assert _ask({"flow.svg": svg, "notes.txt": _edge_text(EDGES)}, question) is None
    # lane FX4 (R1): a question that names no quantity of the comparison ("agree") is not answered by it
    assert _ask({"flow.svg": svg, "notes.txt": _edge_text(EDGES)}, "Do the drawing and the hand-over notes agree?") \
        is None
    q = "Do the connections in the drawing match the notes?"
    three = b"* Ash, Birch and Cedar meet weekly\n" + _edge_text(EDGES)
    assert _ask({"flow.svg": svg, "notes.txt": three}, q) is None        # a line naming three labels
    negated = _edge_text(EDGES[:3]) + b"* Douglas does not hand over to Elm\n"
    assert _ask({"flow.svg": svg, "notes.txt": negated}, q) is None      # a negation is not read
    loose = svg.replace(b"</svg>", b'<line x1="0" y1="200" x2="480" y2="200" stroke="black"/></svg>')
    assert _ask({"flow.svg": loose, "notes.txt": _edge_text(EDGES)}, q) is None   # a line not between two nodes


# ---- X-E: sound onsets against a cue sheet ---------------------------------------------------------------------------

RATE = 8000


def _beeps(onsets, freq=660.0) -> bytes:
    total = int((max(onsets) + 1.0) * RATE)
    x = np.zeros(total)
    n = int(0.2 * RATE)
    t = np.arange(n) / RATE
    for start in onsets:
        a = int(round(start * RATE))
        x[a:a + n] += 0.6 * np.sin(2 * math.pi * freq * t)
    pcm = (np.clip(x, -1, 1) * 32767).astype("<i2").tobytes()
    fmt = struct.pack("<HHIIHH", 1, 1, RATE, RATE * 2, 2, 16)
    body = b"WAVE" + b"fmt " + struct.pack("<I", 16) + fmt + b"data" + struct.pack("<I", len(pcm)) + pcm
    return b"RIFF" + struct.pack("<I", len(body)) + body


def _cues(onsets) -> bytes:
    return "".join(f"{int(s // 60)}:{s % 60:06.3f} bell\n" for s in onsets).encode()


ONSETS = [1.1, 2.75, 4.3, 6.05]


@pytest.mark.parametrize("question", ["Do the bells ring at the times in the cue sheet?",
                                      "Do the bells in the recording start at the cue times?"])
def test_onsets_are_compared_with_a_cue_sheet(question):
    wav = _beeps(ONSETS)
    assert _ask({"bells.wav": wav, "cues.txt": _cues(ONSETS)}, question) is True
    late = ONSETS[:2] + [ONSETS[2] + 0.4] + ONSETS[3:]
    assert _ask({"bells.wav": wav, "cues.txt": _cues(late)}, question) is False
    assert _ask({"bells.wav": _beeps(late), "cues.txt": _cues(ONSETS)}, question) is False


def test_cue_traps_stay_cannot():
    wav = _beeps(ONSETS)
    q = "Do the bells ring at the times in the cue sheet?"
    # lane FX5 (C1): "timing list" names no element compared one by one (no bell, tone or cue)
    assert _ask({"bells.wav": wav, "cues.txt": _cues(ONSETS)}, "Is the timing list right for this recording?") is None
    clock = "".join(f"14:{10 + i:02d}:{5 * i:02d} bell\n" for i in range(4)).encode()      # times of day
    assert _ask({"bells.wav": wav, "cues.txt": clock}, q) is None
    mixed = _cues(ONSETS) + b"then the hall goes quiet\n"
    assert _ask({"bells.wav": wav, "cues.txt": mixed}, q) is None


# ---- X-F: an image's size by axis ----------------------------------------------------------------------------------

def _png(w: int, h: int) -> bytes:
    raw = b"".join(b"\x00" + bytes((x * 3 + y) % 256 for x in range(w)) for y in range(h))

    def chunk(kind, body):
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 0, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def test_pixel_lengths_tied_to_an_axis_are_compared_with_the_measured_size():
    img = _png(90, 34)
    q1 = "Does the caption describe the size of the image correctly?"
    assert _ask({"band.png": img, "caption.txt": b"A band 90 pixels wide and 34 pixels high.\n"}, q1) is True
    assert _ask({"band.png": img, "caption.txt": b"A band 34 pixels high and 90 pixels wide.\n"}, q1) is True
    assert _ask({"band.png": img, "caption.txt": b"A band 96 pixels wide and 34 pixels high.\n"}, q1) is False
    q2 = "Is the image as tall as the note says?"
    assert _ask({"band.png": img, "note.txt": b"Tall banner, 34 px from top to bottom.\n"}, q2) is True
    assert _ask({"band.png": img, "note.txt": b"Tall banner, 40 px from top to bottom.\n"}, q2) is False


def test_a_depiction_question_stays_cannot_even_beside_a_size():
    img = _png(90, 34)
    q = "Does the caption describe what the image shows?"
    for caption in (b"A red kite, 90 pixels wide and 34 pixels high.\n", b"A blue boat, 90 pixels wide and 34 "
                    b"pixels high.\n"):
        assert _ask({"band.png": img, "caption.txt": caption}, q) is None


# ---- X-B: a count question is answered only by a count of that thing -----------------------------------------------

def test_a_question_asking_a_count_is_not_answered_by_a_size():
    img = _png(60, 40)
    q = "Is the number of triangles in the caption the number in the image?"
    for n in (5, 6):
        caption = f"The card shows {n} black triangles on a field of 60 by 40 pixels.\n".encode()
        assert _ask({"card.png": img, "caption.txt": caption}, q) is None


# ---- X-G: chart questions ----------------------------------------------------------------------------------------

def _chart_pair(seed: int, matching: bool, mark: str):
    knobs = {"svg": "trend.svg", "csv": "trend.csv", "series": "Visitors", "x": "month", "n": 9, "mark": mark,
             "scale": "linear"}
    return cross._BUILD["chart_series"](knobs, random.Random(seed), matching, "csv")


@pytest.mark.parametrize("seed", [30001, 30002])
@pytest.mark.parametrize("mark,question", [("points", "Do the dots match the readings in the file?"),
                                           ("line", "Was this line traced from the visitor counts in the sheet?")])
def test_chart_questions_that_name_marks_or_data_counts_are_answered(seed, mark, question):
    assert _ask(_chart_pair(seed, True, mark), question) is True
    assert _ask(_chart_pair(seed, False, mark), question) is False


# ---- X-H: GPX waypoints against stated stops ------------------------------------------------------------------------

def _gpx(n_stops: int) -> bytes:
    wpts = "".join(f'<wpt lat="{50 + i * 0.01}" lon="{8 + i * 0.01}"><name>P{i}</name></wpt>' for i in range(n_stops))
    trk = "".join(f'<trkpt lat="{50 + i * 0.004}" lon="{8 + i * 0.004}"/>' for i in range(30))
    return (f'<?xml version="1.0"?><gpx version="1.1" creator="t" xmlns="http://www.topografix.com/GPX/1/1">{wpts}'
            f"<trk><trkseg>{trk}</trkseg></trk></gpx>").encode()


def test_waypoints_are_counted_as_stops():
    note = b"We stopped 4 times along the river.\n"
    note2 = b"The ride had 4 stops in all.\n"
    q = "Does the route file have as many stops as the note says?"
    assert _ask({"ride.gpx": _gpx(4), "note.txt": note2}, q) is True
    assert _ask({"ride.gpx": _gpx(5), "note.txt": note2}, q) is False
    assert _ask({"ride.gpx": _gpx(4), "note.txt": note}, "How many stops?") in (True, None)


# ---- X-I and X-A: totals stated in words ---------------------------------------------------------------------------

def _plant_csv(values) -> bytes:
    return ("week,Trees\n" + "".join(f"W{i + 1},{v}\n" for i, v in enumerate(values))).encode()


TREES = [37, 52, 41, 66, 29]


def test_a_total_stated_as_a_count_of_the_columns_noun_is_its_sum():
    q = "Is the spring total in the report what the planting log adds up to?"
    report = f"# Spring\n\nVolunteers planted {sum(TREES)} trees this spring.\n".encode()
    assert _ask({"report.md": report, "log.csv": _plant_csv(TREES)}, q) is True
    assert _ask({"report.md": report, "log.csv": _plant_csv(TREES[:-1] + [30])}, q) is False


def test_an_unread_number_about_the_table_withholds_agreement():
    litres = [41.5, 38.25, 50.0, 22.75]
    csv = ("day,Litres\n" + "".join(f"D{i},{v}\n" for i, v in enumerate(litres))).encode()
    q = "Do the litres reported in the note agree with the fill-ups?"
    for stated in (sum(litres), sum(litres) + 7):
        note = (f"The figures below cover {len(litres)} entries.\nThe largest single entry was {max(litres)} "
                f"litres.\nFuel bought over the month: {stated:.2f} litres.\n").encode()
        assert _ask({"note.txt": note, "fills.csv": csv}, q) is None
