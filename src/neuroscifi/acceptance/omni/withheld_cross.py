"""OMNIMIND benchmark, family `cross`: the WITHHELD pairs (lane ZW), written without reading any product code.

DO NOT OPEN, PRINT OR IMPORT THIS FILE unless you are `bench_omni_forms.py final` (COMMON.md, and
docs/quality/OMNIMIND_BENCHMARK_FORMS.md "The withheld split"). Every look is recorded in
docs/quality/evidence/omnimind_final_uses.jsonl before anything is scored; final.py pins this file's SHA-256.

Each pair gives a matching twin (truth True) and a non-matching twin (truth False) that differ in exactly the one
value `change` names, as in cross.py, whose generators build the six existing kinds. Three kinds of pairing the
development and held-out tables do not have are built here, with the standard library only:

  image_caption     a PNG test card of black squares on white, and a caption stating how many squares and the
                    card's size; the twin drops one square from the picture, or states one more in the caption
  route_stops       a GPX file (waypoints for stops, a track) and a trip note stating the number of stops; the twin
                    leaves one waypoint out of the GPX, or states one more stop in the note
  archive_manifest  a zip and a manifest (JSON or text) listing every member with its size in bytes; the twin
                    lengthens one member, or states one size wrong

Data and builders only; nothing here imports product code.
"""
from __future__ import annotations

import hashlib
import io
import json
import random
import struct
import zipfile
import zlib

from . import cross
from .cross_cases import P

SPLIT = "withheld"
NEW_KINDS = ("image_caption", "route_stops", "archive_manifest")
KINDS = cross.KINDS + NEW_KINDS

PAIRS = (
    # ---- a report's total against a CSV column -----------------------------------------------------------------------
    P("xw01", "report_total", 9161, "Do the figures in the spreadsheet add up to the total in the newsletter?", "csv",
      report="newsletter.md", fmt="md", csv="raffle.csv", item="Ticket book", amount="Raised (GBP)",
      n=19, style="currency", decimals=2, sentence="The raffle raised {total} for the roof fund."),
    P("xw02", "report_total", 9162, "Is the season total in the club report what the session log adds up to?",
      "stated", report="club_report.docx", fmt="docx", csv="sessions.csv", item="Session", amount="Laps",
      n=27, style="plain", decimals=0, lo=10, hi=60, sentence="Swimmers completed {total} laps this season."),
    P("xw03", "report_total", 9163, "Does the treasurer's statement agree with the expense lines?", "stated",
      report="treasurer.txt", fmt="txt", csv="expenses.csv", item="Expense", amount="Betrag",
      n=8, style="euro", decimals=2, sentence="Ausgaben insgesamt: {total} EUR"),
    P("xw04", "report_total", 9164, "Check the harvest note's total against the weigh-bridge readings.", "csv",
      report="harvest_note.md", fmt="md", csv="weighbridge.csv", item="Date", amount="Tonnes",
      n=15, style="grouped", decimals=1, lo=2, hi=40, sentence="In all, {total} tonnes of grain went to the store."),
    # ---- a chart against the series in a CSV -------------------------------------------------------------------------
    P("xw05", "chart_series", 9165, "Is this the chart of the numbers in the attached sheet?", "svg",
      svg="levels.svg", csv="levels.csv", series="Reservoir level", x="day", n=18, mark="line", scale="linear",
      metadata=True),
    P("xw06", "chart_series", 9166, "Do the bars match the rows of the CSV?", "csv",
      svg="loans.svg", csv="loans.csv", series="Loans", x="month", n=10, mark="bars", scale="linear"),
    P("xw07", "chart_series", 9167, "Was this log-scale plot drawn from these readings?", "svg",
      svg="spores.svg", csv="spores.csv", series="Spores per m3", x="day", n=16, mark="points", scale="log",
      metadata=True),
    P("xw08", "chart_series", 9168, "Does the line follow the yearly figures in the file?", "csv",
      svg="members.svg", csv="members_by_year.csv", series="Members", x="year", n=11, mark="line", scale="linear"),
    # ---- a sonification against the rows of a table ------------------------------------------------------------------
    P("xw09", "tones_rows", 9169, "Does the soundtrack give every row of the table its own tone?", "wav",
      wav="soundtrack.wav", csv="ridership.csv", column="Riders", n=13, tone_s=0.16, gap_s=0.04, f_lo=240,
      f_hi=960),
    P("xw10", "tones_rows", 9170, "As many tones in the audio as rows in the spreadsheet?", "table",
      wav="tide.wav", csv="tide.csv", column="Height (m)", n=7, tone_s=0.28, gap_s=0.06, f_lo=310, f_hi=620),
    P("xw11", "tones_rows", 9171, "Is each data row heard once in this sound file?", "wav",
      wav="pollen.wav", csv="pollen.csv", column="Grains", n=22, tone_s=0.1, gap_s=0.03, f_lo=280, f_hi=1400),
    P("xw12", "tones_rows", 9172, "Does the audio sonify the whole table and nothing more?", "table",
      wav="queue.wav", csv="checkout.csv", column="Waiting", n=3, tone_s=0.45, gap_s=0.1, f_lo=500, f_hi=750),
    # ---- a README's bounding box against an OBJ mesh -----------------------------------------------------------------
    P("xw13", "mesh_bbox", 9173, "Are the overall dimensions in the notes those of the model?", "vertex",
      readme="NOTES.md", obj="shelf_bracket.obj", shape="l_bracket", size=(90.0, 30.0, 90.0), corner=(0.0, 0.0, 0.0),
      obj_unit="cm", style="prose", axis=0, delta=3.0),
    P("xw14", "mesh_bbox", 9174, "Does the box in the model file have the size the readme gives?", "readme",
      readme="README.md", obj="battery_box.obj", shape="box", size=(64.0, 38.0, 22.0), corner=(-32.0, -19.0, 0.0),
      obj_unit="mm", style="x", axis=1, delta=2.5),
    P("xw15", "mesh_bbox", 9175, "Is the bounding box stated in the readme the one of this mesh?", "vertex",
      readme="README.md", obj="bushing.obj", shape="cylinder", size=(16.0, 16.0, 25.0), corner=(10.0, 10.0, 5.0),
      obj_unit="m", style="minmax", axis=2, delta=1.5),
    P("xw16", "mesh_bbox", 9176, "Do the README's extents fit the wedge in the OBJ?", "readme",
      readme="DOCS.md", obj="door_wedge.obj", shape="wedge", size=(140.0, 35.0, 28.0), corner=(0.0, 0.0, 0.0),
      obj_unit="mm", style="minmax", axis=0, delta=5.0),
    # ---- a Python file against a JSON trace of calls -----------------------------------------------------------------
    P("xw17", "code_trace", 9177, "Can this trace be produced by this module?", "other_fn",
      py="irrigate.py", trace="trace.json", fmt="tree", entry="water",
      graph="water>read_soil,decide,open_valve; read_soil>sample*2; decide>threshold; open_valve>pulse*3",
      unused="drain", event=5, undefined="notify"),
    P("xw18", "code_trace", 9178, "Do the calls in the JSON all appear in the code?", "undefined",
      py="invoice.py", trace="calls.json", fmt="flat", entry="issue",
      graph="issue>number,lines,render,mail; lines>price*2,vat; render>layout", unused="void",
      event=3, undefined="stamp"),
    P("xw19", "code_trace", 9179, "Is the call log consistent with the source?", "undefined",
      py="tuner.py", trace="profile.json", fmt="tree", entry="tune",
      graph="tune>listen,estimate,adjust; listen>window*2; estimate>fft,peak; adjust>step", unused="reset",
      event=6, undefined="calibrate_mic"),
    P("xw20", "code_trace", 9180, "Could running this script give that trace?", "other_fn",
      py="ferry.py", trace="trace.json", fmt="flat", entry="board",
      graph="board>scan_ticket,count,depart; scan_ticket>verify*2; depart>horn", unused="dock",
      event=4, undefined="weigh"),
    # ---- a timeline document against a log ---------------------------------------------------------------------------
    P("xw21", "timeline_log", 9181, "Does the log tell the same order of events as the summary?", "swap",
      doc="summary.md", log="kiln.log", fmt="md", logfmt="iso", noise=7, swap=2,
      events=("kiln switched on", "bisque ramp started", "peak temperature reached", "hold ended", "cooling started",
              "door opened")),
    P("xw22", "timeline_log", 9182, "Is the sequence in my notes the sequence in the log?", "swap",
      doc="notes.txt", log="lift.jsonl", fmt="txt", logfmt="json", noise=5, swap=0,
      events=("lift called", "doors closed", "lift stopped between floors", "alarm pressed", "engineer arrived")),
    P("xw23", "timeline_log", 9183, "Does the gateway log back up the order in the timeline?", "swap",
      doc="timeline.md", log="gateway.log", fmt="md", logfmt="syslog", noise=10, swap=3,
      events=("firmware download began", "checksum verified", "device rebooted", "radio rejoined", "config pushed",
              "telemetry resumed")),
    P("xw24", "timeline_log", 9184, "Do the write-up and the log agree on what came first?", "swap",
      doc="writeup.txt", log="dock.log", fmt="txt", logfmt="iso", noise=4, swap=1,
      events=("ship berthed", "cranes assigned", "unloading started", "customs hold placed")),
    # ---- new: a picture against its caption --------------------------------------------------------------------------
    P("xw25", "image_caption", 9185, "Does the caption describe the picture correctly?", "image",
      png="card.png", doc="caption.md", fmt="md", width=96, height=64, squares=7, side=6),
    P("xw26", "image_caption", 9186, "Is the number of squares in the caption the number in the image?", "caption",
      png="target.png", doc="label.txt", fmt="txt", width=120, height=80, squares=11, side=5),
    P("xw27", "image_caption", 9187, "Check the alt text against the image.", "image",
      png="grid.png", doc="alt.txt", fmt="txt", width=64, height=64, squares=4, side=8),
    P("xw28", "image_caption", 9188, "Does this image show what its description says?", "caption",
      png="tiles.png", doc="description.md", fmt="md", width=150, height=50, squares=9, side=4),
    P("xw29", "image_caption", 9189, "Is the picture consistent with the figure note?", "image",
      png="figure3.png", doc="figure3.md", fmt="md", width=80, height=120, squares=13, side=4),
    # ---- new: a route file against a trip note -----------------------------------------------------------------------
    P("xw30", "route_stops", 9190, "Does the GPX have the stops the trip note mentions?", "gpx",
      gpx="tour.gpx", note="trip.md", fmt="md", stops=6, track=40, center=(11.39, 47.27), route="Inn valley loop"),
    P("xw31", "route_stops", 9191, "Is the number of stops in the note the number in the route file?", "note",
      gpx="walk.gpx", note="walk.txt", fmt="txt", stops=3, track=25, center=(-1.25, 51.75), route="Thames path"),
    P("xw32", "route_stops", 9192, "Do the route and the note agree on how many stops there were?", "gpx",
      gpx="delivery.gpx", note="driver_note.txt", fmt="txt", stops=9, track=60, center=(4.9, 52.37),
      route="Canal ring drop-offs"),
    P("xw33", "route_stops", 9193, "Check the stop count in the summary against the GPX.", "note",
      gpx="survey.gpx", note="summary.md", fmt="md", stops=5, track=33, center=(-122.68, 45.52),
      route="Bridge survey"),
    P("xw34", "route_stops", 9194, "Does the trip file match the stops in the itinerary?", "gpx",
      gpx="itinerary.gpx", note="itinerary.md", fmt="md", stops=4, track=18, center=(151.21, -33.87),
      route="Harbour ferries"),
    # ---- new: an archive against its manifest ------------------------------------------------------------------------
    P("xw35", "archive_manifest", 9195, "Does the manifest list the archive's files with their right sizes?", "zip",
      zip="delivery.zip", manifest="MANIFEST.json", fmt="json", members=6),
    P("xw36", "archive_manifest", 9196, "Is the size of every file in the archive the one the list gives?", "manifest",
      zip="photos.zip", manifest="contents.txt", fmt="txt", members=9),
    P("xw37", "archive_manifest", 9197, "Do the zip and its manifest agree?", "zip",
      zip="release.zip", manifest="manifest.txt", fmt="txt", members=4),
    P("xw38", "archive_manifest", 9198, "Check the packing list against the archive.", "manifest",
      zip="dataset.zip", manifest="packing.json", fmt="json", members=12),
    P("xw39", "archive_manifest", 9199, "Was this manifest written for this archive as it is now?", "zip",
      zip="backup.zip", manifest="index.json", fmt="json", members=7),
)


# ---- image_caption --------------------------------------------------------------------------------------------------

def _png_gray(width: int, height: int, rows: list[bytes]) -> bytes:
    def chunk(kind, body):
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)
    raw = b"".join(b"\x00" + r for r in rows)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def _image_caption(k, rng: random.Random, matching: bool, change: str) -> dict:
    w, h, n, side = k["width"], k["height"], k["squares"], k["side"]
    cell = 2 * side
    cells = [(x, y) for y in range(h // cell) for x in range(w // cell)]
    chosen = rng.sample(cells, n)
    drawn, stated = list(chosen), n
    if not matching:
        if change == "image":
            drawn = drawn[:-1]
        else:
            stated = n + 1
    grid = [bytearray(b"\xff" * w) for _ in range(h)]
    for cx, cy in drawn:
        x0, y0 = cx * cell + side // 2, cy * cell + side // 2
        for y in range(y0, y0 + side):
            grid[y][x0:x0 + side] = b"\x00" * side
    text = f"The test card shows {stated} black squares on a white field of {w} by {h} pixels."
    doc = (f"# {k['png'].rsplit('.', 1)[0].capitalize()}\n\n{text}\n" if k["fmt"] == "md" else text + "\n")
    return {k["png"]: _png_gray(w, h, [bytes(r) for r in grid]), k["doc"]: doc.encode("utf-8")}


# ---- route_stops ----------------------------------------------------------------------------------------------------

def _route_stops(k, rng: random.Random, matching: bool, change: str) -> dict:
    lon0, lat0 = k["center"]
    track = []
    lon, lat = lon0, lat0
    for _ in range(k["track"]):
        lon += rng.uniform(-0.004, 0.006)
        lat += rng.uniform(-0.003, 0.005)
        track.append((round(lon, 6), round(lat, 6)))
    picks = sorted(rng.sample(range(1, k["track"] - 1), k["stops"]))
    stops = [track[i] for i in picks]
    names = [f"Stop {i + 1}" for i in range(len(stops))]
    stated = k["stops"]
    if not matching:
        if change == "gpx":
            stops, names = stops[:-1], names[:-1]
        else:
            stated += 1
    wpts = "".join(f'<wpt lat="{la}" lon="{lo}"><name>{nm}</name></wpt>' for (lo, la), nm in zip(stops, names))
    trkpts = "".join(f'<trkpt lat="{la}" lon="{lo}"/>' for lo, la in track)
    gpx = ('<?xml version="1.0" encoding="UTF-8"?>\n<gpx version="1.1" creator="bench" '
           f'xmlns="http://www.topografix.com/GPX/1/1">{wpts}<trk><name>{k["route"]}</name><trkseg>{trkpts}'
           "</trkseg></trk></gpx>\n")
    line = f"{k['route']}: we made {stated} stops along the way and kept to the planned track."
    note = f"# {k['route']}\n\n{line}\n" if k["fmt"] == "md" else line + "\n"
    return {k["gpx"]: gpx.encode("utf-8"), k["note"]: note.encode("utf-8")}


# ---- archive_manifest -----------------------------------------------------------------------------------------------

_WORDS = ("tidal", "copper", "lantern", "quarry", "meadow", "signal", "harbour", "cinder", "orchid", "plinth")


def _archive_manifest(k, rng: random.Random, matching: bool, change: str) -> dict:
    files = {}
    for i in range(k["members"]):
        folder = ("data", "docs", "img")[i % 3]
        ext = ("csv", "txt", "bin")[i % 3]
        body = " ".join(rng.choice(_WORDS) for _ in range(rng.randint(3, 60))).encode()
        files[f"{folder}/part{i + 1:02d}.{ext}"] = body
    target = sorted(files)[rng.randrange(len(files))]
    extra = rng.randint(1, 40)
    listed = {p: len(b) for p, b in files.items()}
    if not matching:
        if change == "zip":
            files[target] = files[target] + b"#" * extra
        else:
            listed[target] += extra
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for path in sorted(files):
            info = zipfile.ZipInfo(path, date_time=(2024, 5, 1, 12, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, files[path])
    if k["fmt"] == "json":
        manifest = json.dumps({"files": [{"path": p, "bytes": listed[p]} for p in sorted(listed)]}, indent=1) + "\n"
    else:
        manifest = "".join(f"{listed[p]:>8d}  {p}\n" for p in sorted(listed))
    return {k["zip"]: buf.getvalue(), k["manifest"]: manifest.encode("utf-8")}


_BUILD = dict(cross._BUILD, image_caption=_image_caption, route_stops=_route_stops,
              archive_manifest=_archive_manifest)


def build(pair: dict, matching: bool) -> dict:
    """The files of one twin; both twins draw the same values from the pair's seed."""
    rng = random.Random(pair["seed"])
    return _BUILD[pair["kind"]](pair["knobs"], rng, matching, pair["change"])


def tasks() -> list[cross.Task]:
    out = []
    for pair in PAIRS:
        for matching in (True, False):
            out.append(cross.Task(pair["id"] + ("+" if matching else "-"), SPLIT, pair["kind"], build(pair, matching),
                                  pair["question"], matching))
    return out


def digest() -> str:
    """SHA-256 over every generated byte (as cross.digest)."""
    h = hashlib.sha256()
    for t in tasks():
        for name in sorted(t.files):
            h.update(f"{t.id}/{name}/{len(t.files[name])}\n".encode())
            h.update(t.files[name])
    return h.hexdigest()
