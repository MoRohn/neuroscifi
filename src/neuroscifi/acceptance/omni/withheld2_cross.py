"""The SECOND withheld split of the OMNIMIND benchmark (lane ZW2): family `cross`, written after the first withheld look
found the relater answering where it could not know, so that a fix can be judged once on pairs nobody has seen.
Written without reading product code or the first withheld files.

Each entry is one PAIR; it gives two cases (a matching twin and a non-matching twin) that differ in exactly one value.
`truth` is the relation by construction. `expect` is what is scored: the truth where the relation can be decided by
measuring structure and samples (DECIDABLE[kind][0] is True), and None ("cannot") on BOTH twins where one side's
MEANING (what a picture depicts, what a sentence says in another language) cannot be measured by any decoder that reads
only structure and samples. On those kinds any answer is wrong, so a guess gains nothing.

The six kinds of the public splits (cross.py builds them, from new values) and six kinds no public split has (built
here, with the standard library and the benchmark's own encoders). Seeds come from omni.WITHHELD2_SEEDS (9801-9899).
Nothing imports this module except acceptance/omni/final.py (inside its functions) and the lane's own test.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math
import random

import numpy as np

from . import cross, enc

SPLIT = "withheld2"
PUBLIC_KINDS = cross.KINDS
NEW_KINDS = ("image_size_caption", "image_depicts", "translation", "diagram_text", "archive_checksums",
             "audio_cues")
KINDS = PUBLIC_KINDS + NEW_KINDS
# kind -> (can a relater that only measures structure and samples decide it in principle?, why)
DECIDABLE = {
    "report_total": (True, "the stated total is a number in the text; the column's sum is arithmetic"),
    "chart_series": (True, "the marks' positions against the axis ticks give the plotted values"),
    "tones_rows": (True, "tones are counted from the samples, rows from the table"),
    "mesh_bbox": (True, "the bounding box is measured from the vertices"),
    "code_trace": (True, "the calls a function makes are read from the source's syntax"),
    "timeline_log": (True, "the order of events is read from timestamps"),
    "image_size_caption": (True, "the caption states pixel sizes or a frame count, which the image's header and "
                                 "frames give"),
    "image_depicts": (False, "what a drawing depicts is its meaning; pixels and structure do not name an object"),
    "translation": (False, "whether two sentences in two languages say the same is meaning, not structure"),
    "diagram_text": (True, "nodes, their labels and the edges between them are read from the drawing's structure"),
    "archive_checksums": (True, "each member's SHA-256 is computed from its bytes"),
    "audio_cues": (True, "sound onsets are measured from the samples' envelope"),
}


@dataclass(frozen=True)
class Task:
    id: str             # pair id + "+" (matching) or "-" (non-matching)
    split: str
    kind: str
    files: dict
    question: str
    truth: bool         # the relation by construction
    expect: object      # what is scored: truth (decidable kinds) or None, "cannot" (undecidable kinds)


def P(pid, kind, seed, question, change, **knobs):
    return {"id": pid, "kind": kind, "seed": seed, "question": question, "change": change, "knobs": knobs}


PAIRS = (
    # ---- report_total ---------------------------------------------------------------------------------------------
    P("w2x01", "report_total", 9801, "Does the newsletter's total agree with the subscription list?", "stated",
      report="newsletter.md", fmt="md", csv="subscriptions.csv", item="Member", amount="Fee (GBP)", n=17,
      style="currency", decimals=2, lo=3, hi=60, sentence="Altogether the club collected {total} in fees."),
    P("w2x02", "report_total", 9802, "Is the sum in the field report the sum of the plot yields?", "csv",
      report="field_report.docx", fmt="docx", csv="plots.csv", item="Plot", amount="Yield (kg)", n=8,
      style="euro", decimals=1, lo=40, hi=900, sentence="The plots together yielded {total} kg."),
    P("w2x03", "report_total", 9803, "Do the overtime hours in the memo add up?", "stated",
      report="memo_overtime.txt", fmt="txt", csv="overtime.csv", item="Date", amount="Hours", n=13,
      style="plain", decimals=1, lo=1, hi=6, sentence="Overtime logged this cycle: {total} hours in total."),
    P("w2x04", "report_total", 9804, "Does the grant summary state the right total of the awards?", "csv",
      report="grant_summary.md", fmt="md", csv="awards.csv", item="Award", amount="Amount (USD)", n=5,
      style="grouped", decimals=0, lo=1500, hi=48000, sentence="Awards made this round total {total} dollars."),
    P("w2x05", "report_total", 9805, "Check the litres in the fuel note against the fill-ups.", "csv",
      report="fuel_note.txt", fmt="txt", csv="fillups.csv", item="Day", amount="Litres", n=19,
      style="euro", decimals=2, lo=10, hi=70, sentence="Fuel bought over the period: {total} litres."),
    # ---- chart_series ---------------------------------------------------------------------------------------------
    P("w2x06", "chart_series", 9806, "Is this bar chart a picture of the ferry counts?", "svg",
      svg="ferries.svg", csv="ferries.csv", series="Crossings", x="month", n=10, mark="bars", scale="linear",
      metadata=True),
    P("w2x07", "chart_series", 9807, "Do the dots show the river gauge readings in the file?", "csv",
      svg="gauge.svg", csv="gauge.csv", series="Stage", x="day", n=24, mark="points", scale="linear"),
    P("w2x08", "chart_series", 9808, "Was this log-scale line drawn from these citation counts?", "svg",
      svg="citations.svg", csv="citations.csv", series="Citations", x="year", n=14, mark="line", scale="log",
      metadata=True),
    P("w2x09", "chart_series", 9809, "Does the line agree with the spreadsheet of bike hires?", "csv",
      svg="hires.svg", csv="hires.csv", series="Hires", x="year", n=6, mark="line", scale="linear"),
    # ---- tones_rows -----------------------------------------------------------------------------------------------
    P("w2x10", "tones_rows", 9810, "Does the sound play one note for every reading in the table?", "wav",
      wav="readings_audio.wav", csv="readings.csv", column="Humidity (%)", n=11, tone_s=0.18, gap_s=0.07,
      f_lo=280, f_hi=840),
    P("w2x11", "tones_rows", 9811, "Are there as many beeps as rows?", "table",
      wav="beeps.wav", csv="queue_lengths.csv", column="Waiting", n=6, tone_s=0.35, gap_s=0.1, f_lo=350, f_hi=700),
    P("w2x12", "tones_rows", 9812, "Is every row of the sheet heard in the audio?", "table",
      wav="altitude.wav", csv="altitude.csv", column="Altitude (m)", n=14, tone_s=0.16, gap_s=0.05, f_lo=260,
      f_hi=1040),
    # ---- mesh_bbox ------------------------------------------------------------------------------------------------
    P("w2x13", "mesh_bbox", 9813, "Is the clip in the OBJ as large as its notes say?", "readme",
      readme="NOTES.md", obj="cable_clip.obj", shape="l_bracket", size=(18.0, 9.0, 14.0), corner=(0.0, -4.5, 0.0),
      obj_unit="cm", style="prose", axis=0, delta=2.0),
    P("w2x14", "mesh_bbox", 9814, "Do the stated extents match the shelf peg model?", "vertex",
      readme="README.md", obj="peg.obj", shape="cylinder", size=(8.0, 8.0, 35.0), corner=(-4.0, -4.0, 0.0),
      obj_unit="mm", style="minmax", axis=2, delta=3.0),
    P("w2x15", "mesh_bbox", 9815, "Is the ramp model the size given in its description?", "vertex",
      readme="README.md", obj="ramp.obj", shape="wedge", size=(140.0, 60.0, 35.0), corner=(5.0, 5.0, 0.0),
      obj_unit="m", style="x", axis=0, delta=6.0),
    P("w2x16", "mesh_bbox", 9816, "Does the lid mesh fit the dimensions in the notes?", "readme",
      readme="SPEC.md", obj="lid.obj", shape="box", size=(96.0, 64.0, 4.0), corner=(-48.0, -32.0, 10.0),
      obj_unit="mm", style="minmax", axis=1, delta=2.5),
    # ---- code_trace -----------------------------------------------------------------------------------------------
    P("w2x17", "code_trace", 9817, "Could running this module have produced this call log?", "undefined",
      py="thumbnails.py", trace="calls.json", fmt="flat", entry="build",
      graph="build>scan_folder,make_all; make_all>make_one*3; make_one>decode,shrink,encode", unused="purge_cache",
      event=7, undefined="watermark"),
    P("w2x18", "code_trace", 9818, "Is every call recorded in the trace one the code makes?", "other_fn",
      py="mailer.py", trace="trace.json", fmt="tree", entry="send_digest",
      graph="send_digest>gather,compose,deliver; compose>greet,list_items; deliver>connect,transmit*2",
      unused="bounce_report", event=5, undefined="encrypt"),
    P("w2x19", "code_trace", 9819, "Does this trace fit the source?", "other_fn",
      py="backup.py", trace="profile.json", fmt="flat", entry="nightly",
      graph="nightly>snapshot,prune; snapshot>freeze,copy_all,thaw; copy_all>copy*2", unused="restore",
      event=3, undefined="checksum"),
    P("w2x20", "code_trace", 9820, "Is the call tree possible for this program?", "undefined",
      py="scheduler.py", trace="calls.json", fmt="tree", entry="tick",
      graph="tick>due_jobs,run_due; run_due>run_job*2; run_job>lock,execute,unlock", unused="pause",
      event=4, undefined="notify"),
    # ---- timeline_log ---------------------------------------------------------------------------------------------
    P("w2x21", "timeline_log", 9821, "Does the log show these steps in this order?", "swap",
      doc="runbook.md", log="deploy.log", fmt="md", logfmt="json", noise=7, swap=2,
      events=("build started", "tests passed", "image pushed", "canary healthy", "rollout complete")),
    P("w2x22", "timeline_log", 9822, "Do the incident notes and the router log agree on the sequence?", "swap",
      doc="incident.txt", log="router.log", fmt="txt", logfmt="syslog", noise=5, swap=0,
      events=("link down on port 4", "failover to backup uplink", "link up on port 4", "traffic restored")),
    P("w2x23", "timeline_log", 9823, "Is the order in the recipe log the order in the method?", "swap",
      doc="method.md", log="oven.log", fmt="md", logfmt="iso", noise=8, swap=3,
      events=("preheat begun", "dough in", "steam released", "temperature lowered", "loaves out", "oven off")),
    P("w2x24", "timeline_log", 9824, "Does the boarding log follow the order in the summary?", "swap",
      doc="boarding.txt", log="gate.log", fmt="txt", logfmt="iso", noise=4, swap=1,
      events=("gate opened", "priority boarding", "general boarding", "doors closed")),
    # ---- image_size_caption (new; decidable) ----------------------------------------------------------------------
    P("w2x25", "image_size_caption", 9825, "Does the caption describe this image's size correctly?", "caption",
      img="plate_7.png", cap="caption.md", fmt="png", size=(120, 45), stat="size", delta=8,
      text="Plate 7 is a scan {w} pixels wide and {h} pixels high."),
    P("w2x26", "image_size_caption", 9826, "Is the picture as tall as the label says?", "image",
      img="strip.png", cap="label.txt", fmt="png", size=(30, 210), stat="height", delta=14,
      text="Tall strip, {h} px from top to bottom."),
    P("w2x27", "image_size_caption", 9827, "Does the animation have the number of frames its note gives?", "image",
      img="spinner.gif", cap="note.md", fmt="gif", size=(12, 12), frames=6, stat="frames", delta=2,
      text="The spinner loops through {f} frames."),
    P("w2x28", "image_size_caption", 9828, "Are the dimensions in the alt text the image's dimensions?", "caption",
      img="thumb.png", cap="alt.txt", fmt="png", size=(64, 48), stat="size", delta=16,
      text="Alt text: thumbnail, {w} x {h}."),
    # ---- image_depicts (new; NOT decidable by measurement) --------------------------------------------------------
    P("w2x29", "image_depicts", 9829, "Does the caption say what the picture shows?", "caption",
      img="figure_1.png", cap="caption.md", sprite="house", other="a sailing boat",
      text="The drawing shows {thing}."),
    P("w2x30", "image_depicts", 9830, "Is this the right caption for the image?", "caption",
      img="figure_2.png", cap="caption.txt", sprite="boat", other="a pine tree", text="Pictured: {thing}."),
    P("w2x31", "image_depicts", 9831, "Does the description match the drawing?", "caption",
      img="icon.png", cap="description.md", sprite="tree", other="a door key", text="An icon of {thing}."),
    P("w2x32", "image_depicts", 9832, "Is the alt text true to the picture?", "caption",
      img="glyph.png", cap="alt.txt", sprite="key", other="a coffee cup", text="Alt: {thing}, drawn in black."),
    P("w2x33", "image_depicts", 9833, "Does the label name what is drawn?", "caption",
      img="sketch.png", cap="label.md", sprite="cup", other="a house", text="Sketch of {thing}."),
    # ---- translation (new; NOT decidable by measurement) ----------------------------------------------------------
    P("w2x34", "translation", 9834, "Is the French file a faithful translation of the English one?", "target",
      src="notice_en.txt", dst="notice_fr.txt",
      source="The library opens late on rainy evenings, and the garden stays closed until spring.",
      target="La bibliothèque ouvre tard les soirs de pluie, et le jardin reste fermé jusqu'au printemps.",
      changed="La bibliothèque ouvre tard les soirs de pluie, et le jardin reste fermé jusqu'à l'automne."),
    P("w2x35", "translation", 9835, "Does the Spanish text say the same as the English?", "target",
      src="trip_en.md", dst="trip_es.md",
      source="Please bring warm clothes, because the boat leaves before sunrise.",
      target="Por favor, traed ropa de abrigo, porque el barco sale antes del amanecer.",
      changed="Por favor, traed ropa de abrigo, porque el tren sale antes del amanecer."),
    P("w2x36", "translation", 9836, "Is the German sign a correct translation of the English sign?", "target",
      src="sign_en.txt", dst="sign_de.txt",
      source="The baker closes the shop early when the flour runs out.",
      target="Der Bäcker schließt den Laden früh, wenn das Mehl ausgeht.",
      changed="Der Bäcker schließt den Laden früh, wenn der Zucker ausgeht."),
    P("w2x37", "translation", 9837, "Does the Italian rule mean what the English rule means?", "target",
      src="rule_en.txt", dst="rule_it.txt",
      source="Children may swim in the lake only when the flag is green.",
      target="I bambini possono nuotare nel lago solo quando la bandiera è verde.",
      changed="I bambini possono nuotare nel fiume solo quando la bandiera è verde."),
    # ---- diagram_text (new; decidable) ----------------------------------------------------------------------------
    P("w2x38", "diagram_text", 9838, "Does the org chart show the reporting lines in the text?", "svg",
      svg="org.svg", txt="reporting.txt", nodes=("Mira", "Tomas", "Ines", "Kofi", "Lena"),
      edges=((1, 0), (2, 0), (3, 1), (4, 1)), edge=3, to=2, verb="reports to"),
    P("w2x39", "diagram_text", 9839, "Is the dependency diagram the same as the list of dependencies?", "text",
      svg="deps.svg", txt="DEPENDS.md", nodes=("app", "web", "db", "cache", "auth", "log"),
      edges=((0, 1), (0, 4), (1, 2), (1, 3), (4, 5)), edge=1, to=2, verb="depends on"),
    P("w2x40", "diagram_text", 9840, "Do the pipeline drawing and the stage list agree?", "svg",
      svg="pipeline.svg", txt="stages.txt", nodes=("ingest", "clean", "join", "score", "publish"),
      edges=((0, 1), (1, 2), (2, 3), (3, 4)), edge=2, to=4, verb="feeds"),
    P("w2x41", "diagram_text", 9841, "Does the family tree drawing match the written lineage?", "text",
      svg="tree.svg", txt="lineage.md", nodes=("Ada", "Bram", "Cleo", "Dov", "Esi", "Femi", "Gus"),
      edges=((1, 0), (2, 0), (3, 1), (4, 1), (5, 2), (6, 2)), edge=4, to=1, verb="is a child of"),
    # ---- archive_checksums (new; decidable) -----------------------------------------------------------------------
    P("w2x42", "archive_checksums", 9842, "Do the files in the archive match the checksum list?", "member",
      zip="release.zip", sums="SHA256SUMS", members=("bin/tool", "doc/readme.txt", "lib/core.dat"), index=2),
    P("w2x43", "archive_checksums", 9843, "Is the checksum file right for this bundle?", "sums",
      zip="photos.zip", sums="checksums.txt", members=("a/001.raw", "a/002.raw", "b/003.raw", "index.csv"),
      index=0),
    P("w2x44", "archive_checksums", 9844, "Does every member hash to the value in the manifest?", "member",
      zip="dataset.zip", sums="MANIFEST.sha256", members=("train.bin", "test.bin"), index=0),
    P("w2x45", "archive_checksums", 9845, "Are the sums correct for the backup archive?", "sums",
      zip="backup.zip", sums="SUMS", members=("etc/app.conf", "var/state.db", "var/queue.db", "notes.md",
                                              "keys.pub"), index=3),
    # ---- audio_cues (new; decidable) ------------------------------------------------------------------------------
    P("w2x46", "audio_cues", 9846, "Do the chimes in the recording start at the times in the cue sheet?", "wav",
      wav="chimes.wav", cues="cues.txt", n=5, freq=880.0, index=2, shift=0.6),
    P("w2x47", "audio_cues", 9847, "Is the cue list right for this sound file?", "cues",
      wav="signals.wav", cues="cue_list.md", n=4, freq=520.0, index=0, shift=0.5),
    P("w2x48", "audio_cues", 9848, "Do the beeps happen when the timing file says?", "wav",
      wav="beeps.wav", cues="timing.txt", n=6, freq=1200.0, index=5, shift=0.45),
    P("w2x49", "audio_cues", 9849, "Are the onsets in the audio the ones listed?", "cues",
      wav="taps.wav", cues="onsets.txt", n=3, freq=700.0, index=1, shift=0.7),
)

# ---- the new kinds' generators ---------------------------------------------------------------------------------------

SPRITES = {
    "house": (".....##.....", "....####....", "...######...", "..########..", ".##########.", "############",
              ".#........#.", ".#.##..##.#.", ".#.##..##.#.", ".#........#.", ".#..##....#.", ".#..##....#."),
    "boat": (".....#......", ".....##.....", ".....###....", ".....####...", ".....#####..", ".....#......",
             ".....#......", "############", ".##########.", "..########..", "...######...", "............"),
    "tree": (".....##.....", "....####....", "...######...", "....####....", "...######...", "..########..",
             "...######...", "..########..", ".##########.", ".....##.....", ".....##.....", "....####...."),
    "key": ("............", "............", "............", ".###........", "#...#.......", "#...########",
            "#...#..#.#..", ".###...#.#..", "............", "............", "............", "............"),
    "cup": ("............", ".#######....", ".#######.##.", ".#######..#.", ".#######..#.", ".#######.##.",
            ".#######....", "..#####.....", "............", "############", "............", "............"),
}
THINGS = {"house": "a house", "boat": "a sailing boat", "tree": "a pine tree", "key": "a door key",
          "cup": "a coffee cup"}


def sprite_pixels(key: str, scale: int = 8) -> np.ndarray:
    rows = np.array([[0 if ch == "#" else 255 for ch in row] for row in SPRITES[key]], dtype=np.uint8)
    return np.kron(rows, np.ones((scale, scale), dtype=np.uint8))


def _image_size_caption(k, rng, matching, change):
    w, h = k["size"]
    frames = k.get("frames", 1)
    iw, ih, iframes = w, h, frames
    cw, ch, cframes = w, h, frames
    if not matching:
        target = (iw, ih, iframes) if change == "image" else (cw, ch, cframes)
        tw, th, tf = target
        if k["stat"] == "size":
            tw += k["delta"]
        elif k["stat"] == "height":
            th += k["delta"]
        else:
            tf += k["delta"]
        if change == "image":
            iw, ih, iframes = tw, th, tf
        else:
            cw, ch, cframes = tw, th, tf
    yy, xx = np.mgrid[0:ih, 0:iw]
    if k["fmt"] == "png":
        arr = ((xx * 5 + yy * 3 + rng.randrange(40)) % 256).astype(np.uint8)
        image = enc.png(arr, mode="gray")
    else:
        palette = [(v, v, v) for v in (0, 85, 170, 255)]
        image = enc.gif([((xx + yy + i) % 4).astype(np.uint8) for i in range(iframes)], palette, [8] * iframes)
    caption = k["text"].format(w=cw, h=ch, f=cframes) + "\n"
    return {k["img"]: image, k["cap"]: caption.encode("utf-8")}


def _image_depicts(k, rng, matching, change):
    image = enc.png(sprite_pixels(k["sprite"]), mode="gray")
    thing = THINGS[k["sprite"]] if matching else k["other"]
    return {k["img"]: image, k["cap"]: (k["text"].format(thing=thing) + "\n").encode("utf-8")}


def _translation(k, rng, matching, change):
    return {k["src"]: (k["source"] + "\n").encode("utf-8"),
            k["dst"]: ((k["target"] if matching else k["changed"]) + "\n").encode("utf-8")}


def _diagram_svg(nodes, edges) -> bytes:
    n = len(nodes)
    pos = [(60 + 110 * (i % 4), 50 + 90 * (i // 4)) for i in range(n)]
    parts = ['<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 480 240" width="480" height="240">']
    for a, b in edges:
        (x1, y1), (x2, y2) = pos[a], pos[b]
        parts.append(f'<line class="edge" x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="currentColor"/>')
    for i, label in enumerate(nodes):
        x, y = pos[i]
        parts.append(f'<g class="node"><circle cx="{x}" cy="{y}" r="16" fill="#ffffff" stroke="currentColor"/>'
                     f'<text x="{x}" y="{y + 32}" text-anchor="middle">{label}</text></g>')
    parts.append("</svg>")
    return "".join(parts).encode("utf-8")


def _diagram_text(k, rng, matching, change):
    nodes = k["nodes"]
    drawn = [tuple(e) for e in k["edges"]]
    written = list(drawn)
    if not matching:
        a, _ = drawn[k["edge"]]
        target = (drawn if change == "svg" else written)
        target[k["edge"]] = (a, k["to"])
    order = list(written)
    rng.shuffle(order)
    text = "".join(f"- {nodes[a]} {k['verb']} {nodes[b]}\n" for a, b in order)
    return {k["svg"]: _diagram_svg(nodes, drawn), k["txt"]: text.encode("utf-8")}


def _archive_checksums(k, rng, matching, change):
    members = {}
    for name in k["members"]:
        size = rng.randrange(60, 900)
        members[name] = bytes(rng.randrange(256) for _ in range(size))
    sums = {name: hashlib.sha256(data).hexdigest() for name, data in members.items()}
    target = k["members"][k["index"]]
    if not matching:
        if change == "member":
            data = bytearray(members[target])
            at = rng.randrange(len(data))
            data[at] ^= 0x01
            members[target] = bytes(data)
        else:
            digest = sums[target]
            sums[target] = digest[:-1] + ("0" if digest[-1] != "0" else "1")
    text = "".join(f"{sums[name]}  {name}\n" for name in k["members"])
    return {k["zip"]: enc.zip_files(members), k["sums"]: text.encode("utf-8")}


RATE = 8000


def _audio_cues(k, rng, matching, change):
    onsets, t = [], 0.4
    for _ in range(k["n"]):
        t += rng.uniform(1.2, 2.0)
        onsets.append(round(t, 3))
    heard, listed = list(onsets), list(onsets)
    if not matching:
        target = heard if change == "wav" else listed
        target[k["index"]] = round(target[k["index"]] + k["shift"], 3)
    total = int((max(heard) + 1.0) * RATE)
    samples = np.zeros(total)
    burst = int(0.15 * RATE)
    tt = np.arange(burst) / RATE
    ramp = np.minimum(1.0, np.minimum(np.arange(burst), np.arange(burst)[::-1]) / 40.0)
    for onset in heard:
        start = int(round(onset * RATE))
        samples[start:start + burst] += 0.5 * np.sin(2 * math.pi * k["freq"] * tt) * ramp
    lines = []
    for i, onset in enumerate(listed):
        minutes, seconds = divmod(onset, 60)
        lines.append(f"{int(minutes):02d}:{seconds:06.3f}  signal {i + 1}")
    return {k["wav"]: enc.wav(samples, RATE), k["cues"]: ("\n".join(lines) + "\n").encode("utf-8")}


_NEW = {"image_size_caption": _image_size_caption, "image_depicts": _image_depicts, "translation": _translation,
        "diagram_text": _diagram_text, "archive_checksums": _archive_checksums, "audio_cues": _audio_cues}


def build(pair: dict, matching: bool) -> dict:
    """The files of one twin; the random stream is seeded by the pair alone, so both twins draw the same values."""
    rng = random.Random(pair["seed"])
    make = _NEW.get(pair["kind"]) or cross._BUILD[pair["kind"]]
    return make(pair["knobs"], rng, matching, pair["change"])


def tasks() -> list[Task]:
    out = []
    for pair in PAIRS:
        decidable = DECIDABLE[pair["kind"]][0]
        for matching in (True, False):
            out.append(Task(pair["id"] + ("+" if matching else "-"), SPLIT, pair["kind"], build(pair, matching),
                            pair["question"], matching, matching if decidable else None))
    return out


def counts() -> dict:
    """Counts only (what the documents and the report may say)."""
    by_kind = {kind: sum(1 for p in PAIRS if p["kind"] == kind) for kind in KINDS}
    return {"pairs": len(PAIRS), "cases": 2 * len(PAIRS), "pairs_by_kind": by_kind,
            "pairs_in_new_kinds": sum(by_kind[k] for k in NEW_KINDS),
            "undecidable_pairs": sum(by_kind[k] for k in KINDS if not DECIDABLE[k][0]),
            "decidable_by_kind": {k: DECIDABLE[k][0] for k in KINDS}}
