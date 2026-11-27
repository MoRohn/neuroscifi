"""The THIRD withheld split of the OMNIMIND benchmark (lane ZW3): new pairs of the family `cross`.

Written on 2026-11-27 by a lane that never opened the first two withheld splits, never read product code under
`src/neuroscifi/artifacts/` and never read the fix lane's branch or reports. Only `final.py` imports this module
(`scripts/bench_omni_forms.py final --split withheld3 --family cross`); nobody else opens it.

Each pair gives two cases, a matching twin (truth True) and a non-matching twin (truth False), which differ in exactly
one file and one value; the random stream is seeded by the pair alone (seeds from `omni.WITHHELD3_SEEDS`), so both
twins draw the same values. The six public kinds are built by cross.py's generators from new pairs (new values, names
and questions); the eight kinds only this split has are built here.

Some kinds cannot be decided by a relater that only measures structure and samples, because one side's MEANING (what a
module is for, which unit a bare number is in, what mood a sound has) is not measurable from bytes: for those
(`DECIDABLE[kind][0]` is False) the expected answer on BOTH twins is "cannot" (`expect` None), so a guess gains nothing.
Everywhere else `expect` is the truth.
"""
from __future__ import annotations

from dataclasses import dataclass
import io
import json
import math
import random
import struct
import wave

import numpy as np

from . import WITHHELD3_SEEDS, cross, enc

SPLIT = "withheld3"
PUBLIC_KINDS = cross.KINDS
NEW_KINDS = ("array_table", "tree_mirror", "mesh_volume_note", "route_mirror", "animation_timing", "code_intent",
             "unit_claim", "tone_mood")
KINDS = PUBLIC_KINDS + NEW_KINDS
DECIDABLE = {
    **{k: (True, "a public kind: both sides are measurable from the bytes") for k in PUBLIC_KINDS},
    "array_table": (True, "every element of the array and every cell of the table can be read and compared"),
    "tree_mirror": (True, "every leaf of the JSON and of the XML can be read and compared"),
    "mesh_volume_note": (True, "the mesh's volume is computable from its triangles; the note states a number"),
    "route_mirror": (True, "every position of the GPX track and of the GeoJSON line can be read"),
    "animation_timing": (True, "the GIF's frame delays are in its graphic control extensions"),
    "code_intent": (False, "what a module is FOR is not measurable: the names are neutral and the arithmetic fits "
                           "either stated purpose"),
    "unit_claim": (False, "the unit of a bare number is not measurable: the values are plausible in either unit"),
    "tone_mood": (False, "the mood of a sound is not a measurement: the pitches follow no scale"),
}


@dataclass(frozen=True)
class Task:
    id: str             # pair id + "+" (matching) or "-" (non-matching)
    split: str
    kind: str
    files: dict
    question: str
    truth: bool         # by construction (matching twin True)
    expect: bool | None  # the truth where the kind is decidable by measurement, None ("cannot") where it is not


def P(pid, kind, seed, question, change, **knobs):
    return {"id": pid, "kind": kind, "seed": seed, "question": question, "change": change, "knobs": knobs}


S = WITHHELD3_SEEDS.start + 300
PAIRS = (
    # ---- report_total --------------------------------------------------------------------------------------------
    P("w3x01", "report_total", S + 1, "Are the gate takings in the summary what the match rows add up to?", "csv",
      report="season_summary.md", fmt="md", csv="takings_by_match.csv", item="Match", amount="Takings (GBP)", n=11,
      style="currency", decimals=2, lo=300, hi=9000, sentence="Gate takings for the season reached {total} in all."),
    P("w3x02", "report_total", S + 2, "Is the pledge figure in the note the sum of the pledge list?", "stated",
      report="fund_note.txt", fmt="txt", csv="pledge_list.csv", item="Pledge", amount="Amount", n=17, style="plain",
      decimals=0, lo=10, hi=400, sentence="Pledges add up to {total}."),
    P("w3x03", "report_total", S + 3, "Does the letter's cost total agree with the cost lines?", "csv",
      report="audit_letter.docx", fmt="docx", csv="cost_lines.csv", item="Date", amount="Cost (EUR)", n=9,
      style="euro", decimals=2, lo=40, hi=3000, sentence="The costs come to {total} euro in total."),
    P("w3x04", "report_total", S + 4, "Do the trips in the sheet sum to the distance the summary gives?", "stated",
      report="mileage_summary.md", fmt="md", csv="trip_sheet.csv", item="Trip", amount="Km", n=14, style="grouped",
      decimals=1, lo=2, hi=900, sentence="Distance driven this year: {total} km."),
    # ---- chart_series --------------------------------------------------------------------------------------------
    P("w3x05", "chart_series", S + 5, "Do the bars show the monthly ship counts in the table?", "svg",
      svg="harbour_traffic.svg", csv="harbour_traffic.csv", series="Ships", x="month", n=10, mark="bars",
      scale="linear"),
    P("w3x06", "chart_series", S + 6, "Is the line on this log chart the subscriber series in the CSV?", "csv",
      svg="subscribers_plot.svg", csv="subscribers_by_year.csv", series="Subscribers", x="year", n=11, mark="line",
      scale="log"),
    P("w3x07", "chart_series", S + 7, "Are the plotted dots the daily moisture readings in the file?", "svg",
      svg="soil_moisture.svg", csv="soil_moisture.csv", series="Moisture", x="day", n=18, mark="points",
      scale="linear", metadata=True),
    P("w3x08", "chart_series", S + 8, "Does this passenger chart plot the numbers in the spreadsheet?", "csv",
      svg="ferry_counts.svg", csv="ferry_counts.csv", series="Passengers", x="day", n=14, mark="line",
      scale="linear", metadata=True),
    # ---- tones_rows ----------------------------------------------------------------------------------------------
    P("w3x09", "tones_rows", S + 9, "Is every row of the level table heard as one tone?", "table",
      wav="levels_sonified.wav", csv="river_levels.csv", column="Level (m)", n=9, tone_s=0.18, gap_s=0.05, f_lo=260,
      f_hi=820),
    P("w3x10", "tones_rows", S + 10, "Does the sound carry one tone for each day of orders?", "wav",
      wav="orders_audio.wav", csv="orders_by_day.csv", column="Orders", n=13, tone_s=0.12, gap_s=0.04, f_lo=300,
      f_hi=1100),
    P("w3x11", "tones_rows", S + 11, "Are there as many beeps as pulse readings?", "wav",
      wav="pulse_beeps.wav", csv="pulse_readings.csv", column="BPM", n=6, tone_s=0.35, gap_s=0.07, f_lo=350,
      f_hi=700),
    P("w3x12", "tones_rows", S + 12, "Does the tone count equal the number of gust rows?", "table",
      wav="wind_tones.wav", csv="gusts.csv", column="Gust (km/h)", n=11, tone_s=0.2, gap_s=0.06, f_lo=240,
      f_hi=960),
    # ---- mesh_bbox -----------------------------------------------------------------------------------------------
    P("w3x13", "mesh_bbox", S + 13, "Does the hinge model have the extents the part sheet lists?", "vertex",
      readme="PART.md", obj="door_hinge.obj", shape="l_bracket", size=(75.0, 30.0, 45.0), corner=(0.0, 0.0, 0.0),
      obj_unit="cm", style="minmax", axis=0, delta=6.0),
    P("w3x14", "mesh_bbox", S + 14, "Is the tray in the model file as large as the spec says?", "readme",
      readme="SPEC.md", obj="tray.obj", shape="box", size=(140.0, 90.0, 18.0), corner=(-70.0, -45.0, 0.0),
      obj_unit="mm", style="prose", axis=1, delta=4.0),
    P("w3x15", "mesh_bbox", S + 15, "Do the rod's stated limits match its mesh?", "vertex",
      readme="DIMENSIONS.md", obj="rod.obj", shape="cylinder", size=(16.0, 16.0, 120.0), corner=(-8.0, -8.0, 0.0),
      obj_unit="m", style="minmax", axis=2, delta=9.0),
    P("w3x16", "mesh_bbox", S + 16, "Are the ramp's dimensions in the notes those of the OBJ?", "readme",
      readme="about_ramp.md", obj="wheel_ramp.obj", shape="wedge", size=(90.0, 40.0, 35.0), corner=(5.0, 5.0, 0.0),
      obj_unit="mm", style="x", axis=1, delta=3.0),
    # ---- code_trace ----------------------------------------------------------------------------------------------
    P("w3x17", "code_trace", S + 17, "Could the pipeline module have produced this call tree?", "other_fn",
      py="pipeline.py", trace="run_trace.json", fmt="tree", entry="start",
      graph="start>prepare,execute; prepare>validate_input,normalise; execute>step*3,finish", unused="cleanup",
      event=5, undefined="notify"),
    P("w3x18", "code_trace", S + 18, "Is every call logged for the mailer one the source contains?", "undefined",
      py="mail_dispatch.py", trace="calls_log.json", fmt="flat", entry="dispatch",
      graph="dispatch>load_queue,send_all; send_all>render*2,deliver; deliver>connect,transmit", unused="bounce",
      event=6, undefined="throttle"),
    P("w3x19", "code_trace", S + 19, "Does the indexer's trace only show calls its functions make?", "other_fn",
      py="text_indexer.py", trace="trace_out.json", fmt="flat", entry="build",
      graph="build>scan,tokenise,store; tokenise>split_words,lower; store>write_block*2", unused="vacuum", event=3,
      undefined="compress"),
    P("w3x20", "code_trace", S + 20, "Is this execution tree consistent with the scheduler code?", "undefined",
      py="scheduler.py", trace="exec_tree.json", fmt="tree", entry="tick",
      graph="tick>due_jobs,run_job*2; run_job>lock,execute_job,unlock", unused="pause", event=4,
      undefined="escalate"),
    # ---- timeline_log --------------------------------------------------------------------------------------------
    P("w3x21", "timeline_log", S + 21, "Does the router log record the incident steps in the stated order?", "swap",
      doc="incident.md", log="core_router.log", fmt="md", logfmt="syslog", noise=7, swap=2,
      events=("link flapping on port 3", "spanning tree recalculated", "traffic rerouted", "port 3 disabled",
              "alert cleared")),
    P("w3x22", "timeline_log", S + 22, "Do the build log's events come in the sequence the text lists?", "swap",
      doc="sequence.txt", log="ci_build.jsonl", fmt="txt", logfmt="json", noise=9, swap=0,
      events=("checkout complete", "dependencies resolved", "compile finished", "tests passed")),
    P("w3x23", "timeline_log", S + 23, "Is the firing order in the shift notes the order in the kiln log?", "swap",
      doc="shift_notes.md", log="kiln.log", fmt="md", logfmt="iso", noise=6, swap=3,
      events=("kiln lit", "temperature reached 900C", "glaze applied", "door sealed", "cooling started",
              "kiln opened")),
    P("w3x24", "timeline_log", S + 24, "Does the gate log agree with the account of what happened when?", "swap",
      doc="what_happened.txt", log="gate.log", fmt="txt", logfmt="iso", noise=11, swap=1,
      events=("barrier raised", "vehicle entered", "barrier lowered", "ticket printed", "barrier raised again")),
    # ---- array_table (new) ---------------------------------------------------------------------------------------
    P("w3x25", "array_table", S + 25, "Does the array file hold the same numbers as the CSV grid?", "npy",
      npy="matrix_a.npy", csv="matrix_a.csv", shape=(5, 4), dtype="<i4", span=500, header=True),
    P("w3x26", "array_table", S + 26, "Are the kernel values in the CSV the ones stored in the .npy?", "csv",
      npy="kernel.npy", csv="kernel_values.csv", shape=(3, 3), dtype="<f8", span=10, header=False),
    P("w3x27", "array_table", S + 27, "Is the count grid in the table identical to the saved array?", "csv",
      npy="counts_grid.npy", csv="counts_grid.csv", shape=(8, 6), dtype="<i4", span=90, header=True),
    P("w3x28", "array_table", S + 28, "Do the layer weights in both files agree element by element?", "npy",
      npy="weights_layer.npy", csv="weights_layer.csv", shape=(6, 2), dtype="<f8", span=3, header=True),
    # ---- tree_mirror (new) ---------------------------------------------------------------------------------------
    P("w3x29", "tree_mirror", S + 29, "Do the JSON and XML settings carry the same values?", "json",
      json="settings.json", xml="settings.xml",
      sections=(("server", (("port", "int"), ("host", "str"), ("tls", "bool"))),
                ("cache", (("size_mb", "int"), ("policy", "str"))))),
    P("w3x30", "tree_mirror", S + 30, "Is the XML profile a faithful copy of the JSON one?", "xml",
      json="user_profile.json", xml="user_profile.xml",
      sections=(("display", (("width", "int"), ("height", "int"), ("theme", "str"))),
                ("audio", (("volume", "int"), ("muted", "bool"))))),
    P("w3x31", "tree_mirror", S + 31, "Do both station configurations say the same thing?", "xml",
      json="station.json", xml="station.xml",
      sections=(("sensor", (("rate_hz", "int"), ("unit", "str"))), ("uplink", (("retries", "int"),
                                                                                ("secure", "bool"),
                                                                                ("channel", "str"))))),
    P("w3x32", "tree_mirror", S + 32, "Are the limits in the JSON file the limits in the XML file?", "json",
      json="limits.json", xml="limits.xml",
      sections=(("upload", (("max_files", "int"), ("max_mb", "int"))), ("login", (("attempts", "int"),
                                                                               ("lockout", "bool"))))),
    # ---- mesh_volume_note (new) ----------------------------------------------------------------------------------
    P("w3x33", "mesh_volume_note", S + 33, "Is the volume in the print note the volume of the solid?", "stated",
      stl="block.stl", note="print_note.txt", fmt="binary", size=(40.0, 25.0, 12.0), unit="cm3"),
    P("w3x34", "mesh_volume_note", S + 34, "Does the STL enclose as much as the order sheet says?", "mesh",
      stl="crate.stl", note="order_sheet.md", fmt="ascii", size=(60.0, 30.0, 20.0), unit="ml"),
    P("w3x35", "mesh_volume_note", S + 35, "Check the stated material volume against the model.", "mesh",
      stl="spacer_block.stl", note="material.txt", fmt="binary", size=(15.0, 15.0, 8.0), unit="mm3"),
    P("w3x36", "mesh_volume_note", S + 36, "Does the mould hold the volume the card states?", "stated",
      stl="mould.stl", note="card.md", fmt="ascii", size=(90.0, 45.0, 30.0), unit="cm3"),
    # ---- route_mirror (new) --------------------------------------------------------------------------------------
    P("w3x37", "route_mirror", S + 37, "Is the GeoJSON line the same route as the GPX track?", "geojson",
      gpx="ride.gpx", geojson="ride.geojson", n=12, center=(9.18, 45.46), spread=0.08),
    P("w3x38", "route_mirror", S + 38, "Do the two route files trace the same points?", "gpx",
      gpx="walk.gpx", geojson="walk_line.geojson", n=8, center=(-1.55, 47.21), spread=0.03),
    P("w3x39", "route_mirror", S + 39, "Was the line in the GeoJSON exported from this GPX?", "gpx",
      gpx="survey_track.gpx", geojson="survey_line.geojson", n=15, center=(24.94, 60.17), spread=0.05),
    P("w3x40", "route_mirror", S + 40, "Are the track points and the line coordinates the same places?", "geojson",
      gpx="commute.gpx", geojson="commute.geojson", n=10, center=(-122.33, 47.61), spread=0.06),
    # ---- animation_timing (new) ----------------------------------------------------------------------------------
    P("w3x41", "animation_timing", S + 41, "Does each frame last as long as the storyboard says?", "gif",
      gif="spinner.gif", story="storyboard.txt", n=5, size=(8, 6)),
    P("w3x42", "animation_timing", S + 42, "Are the frame durations in the GIF the ones in the timing sheet?",
      "story", gif="blink.gif", story="timing_sheet.md", n=4, size=(6, 6)),
    P("w3x43", "animation_timing", S + 43, "Does the animation's timing follow the plan?", "story",
      gif="walk_cycle.gif", story="plan.txt", n=7, size=(10, 7)),
    P("w3x44", "animation_timing", S + 44, "Is the per-frame timing of this GIF what the notes list?", "gif",
      gif="pulse.gif", story="frame_notes.md", n=6, size=(5, 5)),
    # ---- code_intent (new; undecidable by measurement) -----------------------------------------------------------
    P("w3x45", "code_intent", S + 45, "Does the README describe what this module does?", "readme",
      py="calc.py", readme="CALC_README.md", purposes=("works out the sales tax on an order",
                                                  "works out the delivery charge for an order")),
    P("w3x46", "code_intent", S + 46, "Is this code for what its documentation says?", "readme",
      py="score.py", readme="ABOUT.txt", purposes=("ranks job applicants", "ranks restaurant reviews")),
    P("w3x47", "code_intent", S + 47, "Does the description match the purpose of the script?", "readme",
      py="convert.py", readme="CONVERT_NOTES.md", purposes=("converts recipe quantities for more guests",
                                                    "converts currency amounts at a fixed rate")),
    P("w3x48", "code_intent", S + 48, "Is the module's stated use its real use?", "readme",
      py="adjust.py", readme="USAGE.md", purposes=("adjusts a thermostat schedule",
                                                   "adjusts the brightness of a photo")),
    # ---- unit_claim (new; undecidable by measurement) ------------------------------------------------------------
    P("w3x49", "unit_claim", S + 49, "Are the readings in the unit the note gives?", "note",
      csv="weights.csv", note="weights_note.txt", column="reading", units=("kilograms", "pounds"), lo=55, hi=95, n=10),
    P("w3x50", "unit_claim", S + 50, "Does the stated unit fit the values in the table?", "note",
      csv="temperature_log.csv", note="units.md", column="value", units=("degrees Celsius", "degrees Fahrenheit"), lo=15,
      hi=30, n=12),
    P("w3x51", "unit_claim", S + 51, "Is the unit in the readme the unit of the distance column?", "note",
      csv="legs.csv", note="readme.txt", column="distance", units=("kilometres", "miles"), lo=3, hi=40, n=9),
    P("w3x52", "unit_claim", S + 52, "Are these volumes in the unit the description claims?", "note",
      csv="tank_log.csv", note="description.md", column="volume", units=("litres", "US gallons"), lo=10, hi=60, n=8),
    # ---- tone_mood (new; undecidable by measurement) -------------------------------------------------------------
    P("w3x53", "tone_mood", S + 53, "Does the description of the clip's mood fit the audio?", "note",
      wav="clip.wav", note="description.txt", moods=("cheerful", "mournful"), n=8),
    P("w3x54", "tone_mood", S + 54, "Is the sound as the caption describes it?", "note",
      wav="sting.wav", note="caption.md", moods=("calm", "tense"), n=6),
    P("w3x55", "tone_mood", S + 55, "Does the jingle have the feel the brief asks for?", "note",
      wav="jingle.wav", note="brief.txt", moods=("playful", "solemn"), n=10),
    P("w3x56", "tone_mood", S + 56, "Does the audio match the stated atmosphere?", "note",
      wav="ambience.wav", note="atmosphere.md", moods=("hopeful", "menacing"), n=7),
)


# ---- array_table ------------------------------------------------------------------------------------------------------

def _array_table(k, rng, matching, change):
    rows, cols = k["shape"]
    if k["dtype"] == "<i4":
        values = [[rng.randint(-k["span"], k["span"]) for _ in range(cols)] for _ in range(rows)]
        delta = rng.choice((-1, 1)) * rng.randint(1, 9)
    else:
        values = [[round(rng.uniform(-k["span"], k["span"]), 3) for _ in range(cols)] for _ in range(rows)]
        delta = round(rng.uniform(0.5, 2.0), 3)
    i, j = rng.randrange(rows), rng.randrange(cols)
    arr, tab = [list(r) for r in values], [list(r) for r in values]
    if not matching:
        target = arr if change == "npy" else tab
        target[i][j] = round(target[i][j] + delta, 3) if k["dtype"] == "<f8" else target[i][j] + delta
    npy = enc.npy(np.array(arr, dtype=k["dtype"]))
    lines = [",".join(f"c{c}" for c in range(cols))] if k["header"] else []
    lines += [",".join(repr(v) for v in r) for r in tab]
    return {k["npy"]: npy, k["csv"]: ("\n".join(lines) + "\n").encode()}


# ---- tree_mirror ------------------------------------------------------------------------------------------------------

_WORDS = ("granite", "harrier", "lantern", "meridian", "nimbus", "orchard", "pinnacle", "quarry", "rampart", "sorrel",
          "tundra", "vantage")


def _leaf(kind, rng):
    if kind == "int":
        return rng.randint(1, 5000)
    if kind == "bool":
        return rng.random() < 0.5
    return rng.choice(_WORDS)


def _xml_text(v):
    return ("true" if v else "false") if isinstance(v, bool) else str(v)


def _tree_mirror(k, rng, matching, change):
    tree = {sec: {key: _leaf(kind, rng) for key, kind in keys} for sec, keys in k["sections"]}
    leaves = [(sec, key, kind) for sec, keys in k["sections"] for key, kind in keys]
    sec, key, kind = leaves[rng.randrange(len(leaves))]
    old = tree[sec][key]
    if kind == "int":
        new = old + rng.randint(1, 50)
    elif kind == "bool":
        new = not old
    else:
        new = rng.choice([w for w in _WORDS if w != old])
    js = {s: dict(v) for s, v in tree.items()}
    xs = {s: dict(v) for s, v in tree.items()}
    if not matching:
        (js if change == "json" else xs)[sec][key] = new
    root = k["xml"].rsplit(".", 1)[0]
    body = "".join(f"<{s}>" + "".join(f"<{kk}>{_xml_text(v)}</{kk}>" for kk, v in vals.items()) + f"</{s}>"
                   for s, vals in xs.items())
    xml = f'<?xml version="1.0" encoding="UTF-8"?>\n<{root}>{body}</{root}>\n'
    return {k["json"]: (json.dumps(js, indent=2) + "\n").encode(), k["xml"]: xml.encode()}


# ---- mesh_volume_note --------------------------------------------------------------------------------------------------

_BOX = ((0, 2, 1), (0, 3, 2), (4, 5, 6), (4, 6, 7), (0, 1, 5), (0, 5, 4), (3, 7, 6), (3, 6, 2), (0, 4, 7), (0, 7, 3),
        (1, 2, 6), (1, 6, 5))


def _mesh_volume_note(k, rng, matching, change):
    w, d, h = k["size"]
    verts = [[0.0, 0.0, 0.0], [w, 0.0, 0.0], [w, d, 0.0], [0.0, d, 0.0],
             [0.0, 0.0, h], [w, 0.0, h], [w, d, h], [0.0, d, h]]
    rel = rng.choice((-1, 1)) * rng.uniform(0.15, 0.35)
    volume = w * d * h
    stated = volume
    if not matching:
        if change == "mesh":
            verts[6][2] = 2 * h                     # one top corner raised: the solid stays closed, its volume grows
        else:
            stated = volume * (1 + rel)
    if k["unit"] == "mm3":
        text = f"{stated:.1f} mm³"
    else:
        text = f"{stated / 1000:.3f} " + ("cm³" if k["unit"] == "cm3" else "ml")
    name = k["stl"].rsplit(".", 1)[0].replace("_", " ")
    note = (f"Part: {name}\nMaterial: PLA\nVolume of the solid: {text}\nInfill: 100%\n").encode("utf-8")
    arr = np.array(verts, dtype=float)
    stl = enc.stl_binary(arr, _BOX, header=b"w3 volume") if k["fmt"] == "binary" else enc.stl_ascii(arr, _BOX,
                                                                                                    name="w3")
    return {k["stl"]: stl, k["note"]: note}


# ---- route_mirror -----------------------------------------------------------------------------------------------------

def _route_mirror(k, rng, matching, change):
    cx, cy = k["center"]
    s = k["spread"]
    pts = [[round(cx + rng.uniform(-s, s), 5), round(cy + rng.uniform(-s, s), 5)] for _ in range(k["n"])]
    i = rng.randrange(k["n"])
    shift = round(rng.uniform(0.02, 0.05), 5)
    gpx_pts, geo_pts = [list(p) for p in pts], [list(p) for p in pts]
    if not matching:
        target = gpx_pts if change == "gpx" else geo_pts
        target[i][1] = round(target[i][1] + shift, 5)
    body = "".join(f'<trkpt lat="{lat}" lon="{lon}"/>' for lon, lat in gpx_pts)
    gpx = ('<?xml version="1.0" encoding="UTF-8"?>\n<gpx version="1.1" creator="w3" '
           f'xmlns="http://www.topografix.com/GPX/1/1"><trk><name>route</name><trkseg>{body}</trkseg></trk></gpx>\n')
    geo = {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {"name": "route"},
                                                      "geometry": {"type": "LineString", "coordinates": geo_pts}}]}
    return {k["gpx"]: gpx.encode(), k["geojson"]: json.dumps(geo).encode()}


# ---- animation_timing -------------------------------------------------------------------------------------------------

def _animation_timing(k, rng, matching, change):
    w, h = k["size"]
    delays = [rng.choice((20, 25, 30, 40, 50, 60, 75, 100)) for _ in range(k["n"])]
    i = rng.randrange(k["n"])
    bump = rng.choice((15, 25, 35))
    frames = [np.array([[(x + y + f) % 3 for x in range(w)] for y in range(h)], dtype=np.uint8) for f in range(k["n"])]
    gif_d, story_d = list(delays), list(delays)
    if not matching:
        (gif_d if change == "gif" else story_d)[i] += bump
    gif = enc.gif(frames, [(20, 20, 20), (128, 128, 128), (235, 235, 235)], gif_d)
    lines = [f"Frame {n + 1}: shown for {d / 100:.2f} s" for n, d in enumerate(story_d)]
    head = "# Timing\n\n" if k["story"].endswith(".md") else "Timing of the animation\n\n"
    return {k["gif"]: gif, k["story"]: (head + "\n".join(lines) + "\n").encode()}


# ---- code_intent (undecidable) -----------------------------------------------------------------------------------------

def _code_intent(k, rng, matching, change):
    a, b, c = round(rng.uniform(0.5, 3.0), 2), round(rng.uniform(1, 20), 1), rng.randint(2, 9)
    src = (f'"""Module {k["py"].rsplit(".", 1)[0]}."""\n\n\ndef f_main(x, y):\n    return round(x * {a} + y * {b}, 2)'
           f"\n\n\ndef f_aux(values):\n    return [f_main(v, {c}) for v in values]\n")
    purpose = k["purposes"][0 if matching else 1]
    title = k["py"].rsplit(".", 1)[0]
    readme = f"{title}\n\nThis module {purpose}. Call f_aux with a list of numbers.\n"
    if k["readme"].endswith(".md"):
        readme = "# " + readme
    return {k["py"]: src.encode(), k["readme"]: readme.encode()}


# ---- unit_claim (undecidable) ------------------------------------------------------------------------------------------

def _unit_claim(k, rng, matching, change):
    values = [round(rng.uniform(k["lo"], k["hi"]), 1) for _ in range(k["n"])]
    csv = f"row,{k['column']}\n" + "".join(f"{i + 1},{v}\n" for i, v in enumerate(values))
    unit = k["units"][0 if matching else 1]
    note = f"The {k['column']} column of {k['csv']} is in {unit}.\n"
    if k["note"].endswith(".md"):
        note = "# Units\n\n" + note
    return {k["csv"]: csv.encode(), k["note"]: note.encode()}


# ---- tone_mood (undecidable) -------------------------------------------------------------------------------------------

def _tone_mood(k, rng, matching, change):
    rate = 8000
    frames = bytearray()
    for _ in range(k["n"]):
        f = rng.uniform(200, 900)                                    # no scale: pitches drawn from a continuum
        n = int(rng.uniform(0.15, 0.4) * rate)
        for t in range(n):
            env = min(1.0, t / 80, (n - 1 - t) / 80)
            frames += struct.pack("<h", int(round(0.5 * 32767 * env * math.sin(2 * math.pi * f * t / rate))))
        frames += b"\x00\x00" * int(0.05 * rate)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wv:
        wv.setnchannels(1)
        wv.setsampwidth(2)
        wv.setframerate(rate)
        wv.writeframes(bytes(frames))
    mood = k["moods"][0 if matching else 1]
    note = f"The clip sounds {mood}.\n"
    if k["note"].endswith(".md"):
        note = "# About the clip\n\n" + note
    return {k["wav"]: buf.getvalue(), k["note"]: note.encode()}


_BUILD = {"array_table": _array_table, "tree_mirror": _tree_mirror, "mesh_volume_note": _mesh_volume_note,
          "route_mirror": _route_mirror, "animation_timing": _animation_timing, "code_intent": _code_intent,
          "unit_claim": _unit_claim, "tone_mood": _tone_mood}


def build(pair: dict, matching: bool) -> dict:
    """The files of one twin; both twins draw the same random stream (seeded by the pair alone)."""
    if pair["seed"] not in WITHHELD3_SEEDS:
        raise ValueError(f"seed {pair['seed']} is not in WITHHELD3_SEEDS")
    if pair["kind"] in PUBLIC_KINDS:
        return cross.build(pair, matching)
    return _BUILD[pair["kind"]](pair["knobs"], random.Random(pair["seed"]), matching, pair["change"])


def tasks() -> list[Task]:
    """Matching twin first, then its non-matching twin, pair by pair (final.py scores twins as rows 2i, 2i+1)."""
    out = []
    for pair in PAIRS:
        decidable = DECIDABLE[pair["kind"]][0]
        for matching in (True, False):
            out.append(Task(pair["id"] + ("+" if matching else "-"), SPLIT, pair["kind"], build(pair, matching),
                            pair["question"], matching, matching if decidable else None))
    return out
