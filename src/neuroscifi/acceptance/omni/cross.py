"""OMNIMIND benchmark, family `cross`: questions that need two artifacts of different kinds (lane ZF; docs/quality/
OMNIMIND_BENCHMARK_FORMS.md has the design and what the measures cannot show).

Every case is built here, by hand, with the standard library (struct, zlib, wave, zipfile, json), from a frozen pair in
cross_cases.py. Each pair gives two cases, a matching twin (truth True) and a non-matching twin (truth False), which
differ in exactly the one value the pair's `change` names; the truth is known by construction, never by reading the
files back. tests/acceptance/test_omni_forms_bench.py re-derives every truth with its own readers.

The subject: `relate(files: {name: bytes}, question: str) -> {"answer": bool | None, "because": [str, ...]}` (an object
with .relate or a plain callable). None means "cannot tell". Today's system has no such function: its baseline is
"cannot" on every case. Nothing here imports product code.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
import math
import random
import struct
import wave
from xml.sax.saxutils import escape
import zipfile

from . import cross_cases

SPLITS = ("development", "held_out")
KINDS = ("report_total", "chart_series", "tones_rows", "mesh_bbox", "code_trace", "timeline_log")
LABEL = ("OMNIMIND benchmark, family cross: file pairs built by the benchmark's own generators, truth known by "
         "construction; each pair has a matching and a non-matching twin that differ in one value; offline; no model")


@dataclass(frozen=True)
class Task:
    id: str             # pair id + "+" (matching) or "-" (non-matching)
    split: str
    kind: str
    files: dict
    question: str
    truth: bool


def _num(x: float, places: int = 6) -> str:
    """A plain decimal with no trailing zeros: 12.50 -> '12.5', 3.0 -> '3'."""
    text = f"{x:.{places}f}".rstrip("0").rstrip(".")
    return "0" if text in ("-0", "") else text


# ---- report_total ----------------------------------------------------------------------------------------------------

def _money(minor: int, decimals: int, style: str, header: str) -> str:
    scale = 10 ** decimals
    whole, frac = divmod(minor, scale)
    tail = f"{frac:0{decimals}d}" if decimals else ""
    if style == "euro":
        return f"{whole:,}".replace(",", ".") + ("," + tail if decimals else "")
    if style == "plain":
        return f"{whole}" + ("." + tail if decimals else "")
    text = f"{whole:,}" + ("." + tail if decimals else "")
    if style == "currency":
        symbol = "$" if "USD" in header else "£" if "GBP" in header else "€"
        return symbol + text
    return text                                                                         # grouped


def _csv_number(minor: int, decimals: int, style: str) -> str:
    plain = _money(minor, decimals, "plain", "")
    if style == "euro":
        return plain.replace(".", ",")
    if style == "grouped":
        return '"' + _money(minor, decimals, "grouped", "") + '"'                        # quoted with separators
    return plain


def _docx(paragraphs) -> bytes:
    body = "".join(f"<w:p><w:r><w:t xml:space=\"preserve\">{escape(p)}</w:t></w:r></w:p>" for p in paragraphs)
    parts = {
        "[Content_Types].xml": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.'
            'wordprocessingml.document.main+xml"/></Types>'),
        "_rels/.rels": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/'
            'officeDocument" Target="word/document.xml"/></Relationships>'),
        "word/document.xml": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            f'<w:body>{body}</w:body></w:document>'),
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, text in parts.items():
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, text.encode("utf-8"))
    return buf.getvalue()


def _report_total(k: dict, rng: random.Random, matching: bool, change: str) -> dict:
    d, style = k["decimals"], k["style"]
    scale = 10 ** d
    lo, hi = k.get("lo", 5), k.get("hi", 2500)
    values = [rng.randint(lo * scale, hi * scale) for _ in range(k["n"])]
    row = rng.randrange(k["n"])
    delta = rng.randint(1, 20) * scale
    stated = sum(values)
    if not matching:
        if change == "csv":
            values[row] += delta
        else:
            stated += delta
    sep = ";" if style == "euro" else ","
    labels = [(datetime(2026, 1, 5) + timedelta(days=7 * i)).date().isoformat() if k["item"] in ("Day", "Date")
              else f"{k['item']} {i + 1}" for i in range(k["n"])]
    csv = sep.join((k["item"], k["amount"])) + "\n" + "".join(
        f"{label}{sep}{_csv_number(v, d, style)}\n" for label, v in zip(labels, values))
    title = k["report"].rsplit(".", 1)[0].replace("_", " ").capitalize()
    total = _money(stated, d, style, k["amount"])
    largest = _money(max(values), d, style, k["amount"])
    lines = [f"The figures below cover {k['n']} entries.", f"The largest single entry was {largest}.",
             k["sentence"].format(total=total), "Please get in touch with any questions."]
    if k["fmt"] == "docx":
        report = _docx([title, *lines])
    elif k["fmt"] == "md":
        report = (f"# {title}\n\n" + "\n\n".join(lines) + "\n").encode("utf-8")
    else:
        report = (title.upper() + "\n\n" + "\n".join(lines) + "\n").encode("utf-8")
    return {k["report"]: report, k["csv"]: csv.encode("utf-8")}


# ---- chart_series ----------------------------------------------------------------------------------------------------

_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
_L, _R, _T, _B = 70.0, 610.0, 30.0, 350.0


def _nice_step(span: float, target: int = 5) -> float:
    raw = span / target
    power = 10 ** math.floor(math.log10(raw))
    for m in (1, 2, 5, 10):
        if raw <= m * power:
            return m * power
    return 10 * power


def _series_values(k, rng):
    n = k["n"]
    if k["scale"] == "log":
        level = rng.uniform(0.6, 1.5)
        out = []
        for _ in range(n):
            level += rng.uniform(-0.25, 0.45)
            out.append(round(10 ** min(max(level, 0.3), 4.5), 1))
        return out
    level = rng.uniform(40, 160)
    out = []
    for _ in range(n):
        level = max(5.0, level + rng.uniform(-15, 18))
        out.append(round(level, 1))
    return out


def _x_labels(k):
    if k["x"] == "year":
        return ("Year", [str(2010 + i) for i in range(k["n"])])
    if k["x"] == "month":
        return ("Month", list(_MONTHS[:k["n"]]))
    return ("Date", [(datetime(2026, 3, 1) + timedelta(days=i)).date().isoformat() for i in range(k["n"])])


def _svg_chart(k, xs, values, metadata_rows) -> bytes:
    n = len(values)
    bars = k["mark"] == "bars"
    if bars or k["x"] == "month":
        band = (_R - _L) / n
        px = [_L + (i + 0.5) * band for i in range(n)]
    else:
        px = [_L + i * (_R - _L) / (n - 1) for i in range(n)]
    if k["scale"] == "log":
        y0 = 10 ** math.floor(math.log10(min(values)))
        y1 = 10 ** math.ceil(math.log10(max(values)))
        if y1 / y0 < 100:                                   # at least three ticks, or log cannot be told from linear
            y1 = y0 * 100
        ticks = [10 ** e for e in range(round(math.log10(y0)), round(math.log10(y1)) + 1)]

        def py(v):
            return _B - (math.log10(v) - math.log10(y0)) / (math.log10(y1) - math.log10(y0)) * (_B - _T)
    else:
        step = _nice_step(max(values) - (0 if bars else min(values)))
        y0 = 0.0 if bars else math.floor(min(values) / step) * step
        y1 = math.ceil(max(values) / step) * step
        ticks = [y0 + i * step for i in range(int(round((y1 - y0) / step)) + 1)]

        def py(v):
            return _B - (v - y0) / (y1 - y0) * (_B - _T)
    every = 1 if n <= 12 else max(1, math.ceil(n / 8))
    xt = "".join(f'<g class="tick" transform="translate({px[i]:.2f},{_B:.2f})"><line y2="5"/>'
                 f'<text y="18" text-anchor="middle">{escape(xs[i])}</text></g>' for i in range(0, n, every))
    yt = "".join(f'<g class="tick" transform="translate({_L:.2f},{py(t):.2f})"><line x2="-5"/>'
                 f'<text x="-8" dy="4" text-anchor="end">{_num(t)}</text></g>' for t in ticks)
    name = escape(k["series"], {'"': "&quot;"})
    if k["mark"] == "line":
        pts = " ".join(f"{px[i]:.2f},{py(v):.2f}" for i, v in enumerate(values))
        marks = f'<polyline class="series" fill="none" stroke="currentColor" points="{pts}"/>'
    elif k["mark"] == "points":
        marks = "".join(f'<circle class="mark" cx="{px[i]:.2f}" cy="{py(v):.2f}" r="3"/>'
                        for i, v in enumerate(values))
    else:
        width = (_R - _L) / n * 0.7
        base = py(y0)
        marks = "".join(f'<rect class="mark" x="{px[i] - width / 2:.2f}" y="{py(v):.2f}" width="{width:.2f}" '
                        f'height="{base - py(v):.2f}"/>' for i, v in enumerate(values))
    meta = ""
    if metadata_rows is not None:
        meta = "<metadata>" + escape(json.dumps({"columns": list(metadata_rows[0]), "rows": metadata_rows[1]})) + \
               "</metadata>"
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 640 400" width="640" height="400">'
           f'<title>{escape(k["series"])}</title>{meta}'
           f'<g class="axis x">{xt}</g><g class="axis y">{yt}</g>'
           f'<g class="plot"><g class="series" data-name="{name}">{marks}</g></g></svg>')
    return svg.encode("utf-8")


def _chart_series(k, rng, matching, change):
    values = _series_values(k, rng)
    j = rng.randrange(1, k["n"] - 1)
    other = list(values)
    if k["scale"] == "log":
        other[j] = round(other[j] * 4 if other[j] < 3000 else other[j] / 4, 1)
    else:
        bump = round(0.3 * (max(values) - min(values)) + 10, 1)
        other[j] = round(other[j] + bump if other[j] - bump < 5 else other[j] - bump, 1)
    plotted, tabled = values, values
    if not matching:
        plotted, tabled = (other, values) if change == "svg" else (values, other)
    xname, xs = _x_labels(k)
    csv = f"{xname},{k['series']}\n" + "".join(f"{x},{_num(v, 1)}\n" for x, v in zip(xs, tabled))
    # the metadata (when present) always holds the un-changed series: a subject that reads it instead of the marks
    # is fooled exactly when the marks were changed
    meta = ((xname, k["series"]), [[x, v] for x, v in zip(xs, values)]) if k.get("metadata") else None
    return {k["svg"]: _svg_chart(k, xs, plotted, meta), k["csv"]: csv.encode("utf-8")}


# ---- tones_rows ------------------------------------------------------------------------------------------------------

RATE = 8000


def _wav(values, k) -> bytes:
    lo, hi = min(values), max(values)
    n_tone, n_gap, fade = int(k["tone_s"] * RATE), int(k["gap_s"] * RATE), int(0.005 * RATE)
    frames = bytearray(b"\x00\x00" * int(0.1 * RATE))
    for v in values:
        f = k["f_lo"] + (0 if hi == lo else (v - lo) / (hi - lo)) * (k["f_hi"] - k["f_lo"])
        for i in range(n_tone):
            env = min(1.0, i / fade, (n_tone - 1 - i) / fade)
            frames += struct.pack("<h", int(round(0.6 * 32767 * env * math.sin(2 * math.pi * f * i / RATE))))
        frames += b"\x00\x00" * n_gap
    frames += b"\x00\x00" * int(0.1 * RATE)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(bytes(frames))
    return buf.getvalue()


def _tones_rows(k, rng, matching, change):
    values = [round(rng.uniform(0, 100), 1) for _ in range(k["n"])]
    extra = round(rng.uniform(0, 100), 1)
    table, tones = list(values), list(values)
    if not matching:
        if change == "table":
            table.append(extra)
        else:
            tones = tones[:-1]
    csv = f"Index,{k['column']}\n" + "".join(f"{i + 1},{_num(v, 1)}\n" for i, v in enumerate(table))
    return {k["wav"]: _wav(tones, k), k["csv"]: csv.encode("utf-8")}


# ---- mesh_bbox -------------------------------------------------------------------------------------------------------

_UNIT = {"mm": (1.0, "millimetres"), "cm": (0.1, "centimetres"), "m": (0.001, "metres")}


def _mesh(shape, w, d, h):
    if shape == "box":
        v = [(x, y, z) for z in (0, h) for y in (0, d) for x in (0, w)]
        f = [(1, 3, 2), (2, 3, 4), (5, 6, 8), (5, 8, 7), (1, 2, 6), (1, 6, 5), (3, 7, 8), (3, 8, 4),
             (1, 5, 7), (1, 7, 3), (2, 4, 8), (2, 8, 6)]
        return v, f
    if shape == "wedge":
        prof = [(0, 0), (w, 0), (0, h)]
    elif shape == "l_bracket":
        t = round(min(w, h) * 0.2, 3)
        prof = [(0, 0), (w, 0), (w, t), (t, t), (t, h), (0, h)]
    else:                                                                               # cylinder
        seg = 24
        ring = [(w / 2 + w / 2 * math.cos(2 * math.pi * i / seg), d / 2 + d / 2 * math.sin(2 * math.pi * i / seg))
                for i in range(seg)]
        v = [(x, y, 0.0) for x, y in ring] + [(x, y, h) for x, y in ring] + [(w / 2, d / 2, 0.0), (w / 2, d / 2, h)]
        f = []
        for i in range(seg):
            a, b = i + 1, (i + 1) % seg + 1
            f += [(a, b, b + seg), (a, b + seg, a + seg), (2 * seg + 1, b, a), (2 * seg + 2, a + seg, b + seg)]
        return v, f
    m = len(prof)
    v = [(x, 0.0, z) for x, z in prof] + [(x, d, z) for x, z in prof]
    f = []
    for i in range(m):
        a, b = i + 1, (i + 1) % m + 1
        f += [(a, b, b + m), (a, b + m, a + m)]
    for i in range(1, m - 1):                       # caps as a fan (the L's fan from vertex 1 stays inside it)
        f += [(1, i + 2, i + 1), (1 + m, i + 1 + m, i + 2 + m)]
    return v, f


def _mesh_bbox(k, rng, matching, change):
    w, d, h = k["size"]
    ox, oy, oz = k["corner"]
    verts, faces = _mesh(k["shape"], w, d, h)
    verts = [[x + ox, y + oy, z + oz] for x, y, z in verts]
    stated_size = [w, d, h]
    stated_min = [ox, oy, oz]
    stated_max = [ox + w, oy + d, oz + h]
    axis, delta = k["axis"], k["delta"]
    if not matching:
        if change == "vertex":
            top = max(v[axis] for v in verts)
            index = next(i for i, v in enumerate(verts) if v[axis] == top)
            verts[index][axis] += delta
        else:
            stated_size[axis] += delta
            stated_max[axis] += delta
    factor, unit_name = _UNIT[k["obj_unit"]]
    places = {"mm": 3, "cm": 4, "m": 6}[k["obj_unit"]]
    obj = [f"# {k['obj'].rsplit('.', 1)[0]}", f"# units: {unit_name}", "o part"]
    obj += [f"v {_num(x * factor, places)} {_num(y * factor, places)} {_num(z * factor, places)}" for x, y, z in verts]
    obj += [f"f {a} {b} {c}" for a, b, c in faces]
    name = k["obj"].rsplit(".", 1)[0].replace("_", " ")
    s = [_num(x, 3) for x in stated_size]
    if k["style"] == "x":
        size = f"Overall size: {s[0]} x {s[1]} x {s[2]} mm (width x depth x height)."
    elif k["style"] == "minmax":
        lo, hi = [_num(x, 3) for x in stated_min], [_num(x, 3) for x in stated_max]
        size = f"Bounding box (mm): x {lo[0]} to {hi[0]}, y {lo[1]} to {hi[1]}, z {lo[2]} to {hi[2]}."
    else:
        size = f"The part is {s[0]} mm wide, {s[1]} mm deep and {s[2]} mm tall."
    wall = rng.choice((1.5, 2.0, 2.5, 3.0))
    readme = (f"# {name.capitalize()}\n\nPrinted in PETG.\n\n## Dimensions\n\n{size}\n\n"
              f"Minimum wall thickness: {_num(wall)} mm. The model file is `{k['obj']}`.\n")
    return {k["readme"]: readme.encode("utf-8"), k["obj"]: ("\n".join(obj) + "\n").encode("utf-8")}


# ---- code_trace ------------------------------------------------------------------------------------------------------

def _graph(spec: str):
    """'a>b,c*3; c>d' -> ordered functions and {caller: [(callee, times)]}."""
    calls, order = {}, []
    for part in spec.split(";"):
        caller, _, rest = part.strip().partition(">")
        calls[caller] = []
        for item in rest.split(","):
            name, _, times = item.strip().partition("*")
            calls[caller].append((name, int(times or 1)))
        for fn in [caller] + [c for c, _ in calls[caller]]:
            if fn not in order:
                order.append(fn)
    return order, calls


def _source(module: str, order, calls, unused) -> str:
    lines = [f'"""{module}: a small module (benchmark fixture)."""']
    for fn in order + [unused]:
        lines += ["", "", f"def {fn}(data):"]
        body = ["    result = data"]
        for callee, times in calls.get(fn, []):
            if times > 1:
                body += [f"    for _ in range({times}):", f"        result = {callee}(result)"]
            else:
                body.append(f"    result = {callee}(result)")
        lines += (body + ["    return result"]) if fn in calls else ["    return data"]
    return "\n".join(lines) + "\n"


def _walk(fn, calls, out, depth=0):
    node = {"function": fn, "calls": []}
    for callee, times in calls.get(fn, []):
        for _ in range(times):
            out.append((fn, callee))
            node["calls"].append(_walk(callee, calls, out, depth + 1))
    return node


def _code_trace(k, rng, matching, change):
    order, calls = _graph(k["graph"])
    module = k["py"].rsplit(".", 1)[0]
    events = []
    root = _walk(k["entry"], calls, events)
    if not matching:
        i = k["event"] - 1                                  # 1-based position among the calls, in call order
        caller, callee = events[i]
        if change == "undefined":
            new = k["undefined"]
        else:
            callees = {c for c, _ in calls.get(caller, [])}
            new = sorted(f for f in order + [k["unused"]] if f not in callees and f not in (caller, k["entry"]))[0]
        events[i] = (caller, new)
        seen = [0]

        def rename(node):
            for child in node["calls"]:
                if seen[0] == i:
                    child["function"] = new
                seen[0] += 1
                rename(child)
        rename(root)
    if k["fmt"] == "flat":
        trace = {"module": module, "events": [{"seq": n + 1, "caller": a, "callee": b}
                                              for n, (a, b) in enumerate(events)]}
    else:
        trace = {"module": module, "root": root}
    return {k["py"]: _source(module, order, calls, k["unused"]).encode("utf-8"),
            k["trace"]: (json.dumps(trace, indent=1) + "\n").encode("utf-8")}


# ---- timeline_log ----------------------------------------------------------------------------------------------------

_NOISE = ("heartbeat ok", "metrics flushed", "session count 12", "cache hit ratio 0.93", "disk usage 61%",
          "ntp sync ok", "worker idle", "queue depth 0", "certificate valid for 41 days", "backup verified",
          "fan speed 2100 rpm", "watchdog ok")


def _timeline_log(k, rng, matching, change):
    events = list(k["events"])
    start = datetime(2026, rng.randint(1, 11), rng.randint(1, 28), rng.randint(0, 22), rng.randint(0, 59),
                     tzinfo=timezone.utc)
    times, t = [], start
    for _ in events:
        t = t + timedelta(seconds=rng.randint(20, 900))
        times.append(t)
    lines = [(times[i], events[i]) for i in range(len(events))]
    noise_times = [start + timedelta(seconds=rng.randint(0, int((times[-1] - start).total_seconds()) + 120))
                   for _ in range(k["noise"])]
    noise = [rng.choice(_NOISE) for _ in range(k["noise"])]
    i = k["swap"]
    if not matching:
        nxt = times[i + 1]
        limit = times[i + 2] if i + 2 < len(times) else nxt + timedelta(seconds=120)
        moved = min(nxt + timedelta(seconds=rng.randint(5, 60)), limit - timedelta(seconds=1))
        lines[i] = (moved, events[i])
    taken = {when for when, _ in lines}
    entries = list(lines)
    for when, msg in zip(noise_times, noise):
        while when in taken:
            when += timedelta(seconds=1)
        taken.add(when)
        entries.append((when, msg))
    entries.sort(key=lambda e: e[0])
    source = k["log"].rsplit(".", 1)[0]
    out = []
    for when, msg in entries:
        iso = when.strftime("%Y-%m-%dT%H:%M:%SZ")
        if k["logfmt"] == "iso":
            out.append(f"{iso} INFO {source}: {msg}")
        elif k["logfmt"] == "syslog":
            out.append(f"{when.strftime('%b')} {when.day:2d} {when.strftime('%H:%M:%S')} site-gw "
                       f"{source}[{4000 + len(source)}]: {msg}")
        else:
            out.append(json.dumps({"ts": iso, "level": "info", "msg": msg}))
    if k["fmt"] == "md":
        doc = "# Timeline\n\n" + "".join(f"{n + 1}. {e[0].upper() + e[1:]}\n" for n, e in enumerate(events))
    else:
        words = ["First, "] + ["Then "] * (len(events) - 2) + ["Finally, "]
        doc = "What happened, in order:\n\n" + "".join(f"{w}{e}.\n" for w, e in zip(words, events))
    return {k["doc"]: doc.encode("utf-8"), k["log"]: ("\n".join(out) + "\n").encode("utf-8")}


_BUILD = {"report_total": _report_total, "chart_series": _chart_series, "tones_rows": _tones_rows,
          "mesh_bbox": _mesh_bbox, "code_trace": _code_trace, "timeline_log": _timeline_log}


def build(pair: dict, matching: bool) -> dict:
    """The files of one twin. The random stream is seeded by the pair alone, so both twins draw the same values and
    differ only where `matching` makes them."""
    rng = random.Random(pair["seed"])
    return _BUILD[pair["kind"]](pair["knobs"], rng, matching, pair["change"])


def tasks(split: str) -> list[Task]:
    if split not in SPLITS:
        raise ValueError(f"split must be one of {SPLITS}")
    out = []
    for pair in cross_cases.ALL[split]:
        for matching in (True, False):
            out.append(Task(pair["id"] + ("+" if matching else "-"), split, pair["kind"], build(pair, matching),
                            pair["question"], matching))
    return out


def digest(split: str) -> str:
    """SHA-256 over every generated byte of a split (pinned by the test: the generators may not drift)."""
    h = hashlib.sha256()
    for t in tasks(split):
        for name in sorted(t.files):
            h.update(f"{t.id}/{name}/{len(t.files[name])}\n".encode())
            h.update(t.files[name])
    return h.hexdigest()


# ---- references and scoring ------------------------------------------------------------------------------------------

REFERENCES = {
    "today": ("today's system: no function relates two artifacts, so every answer is 'cannot'",
              lambda files, question: {"answer": None, "because": ["no such capability"]}),
    "always_consistent": ("always says they agree", lambda files, question: {"answer": True, "because": []}),
    "always_inconsistent": ("always says they disagree", lambda files, question: {"answer": False, "because": []}),
}


def _frac(k, n):
    return {"k": k, "n": n, "share": round(k / n, 4) if n else None}


def score(subject, split: str, *, detail: bool = False) -> dict:
    if isinstance(subject, str):
        subject = REFERENCES[subject][1]
    relate = getattr(subject, "relate", subject)
    rows = []
    for t in tasks(split):
        reply = relate(dict(t.files), t.question) or {}
        answer = reply.get("answer")
        answer = answer if isinstance(answer, bool) else None
        rows.append((t, answer, bool(reply.get("because"))))
    n = len(rows)

    def block(sel):
        m = len(sel)
        answered = [r for r in sel if r[1] is not None]
        correct = sum(1 for t, a, _ in sel if a is t.truth)
        return {"cases": m, "correct": _frac(correct, m), "answered": _frac(len(answered), m),
                "correct_of_answered": _frac(correct, len(answered))}
    out = {"label": LABEL, "split": split, "n_cases": n, "n_pairs": n // 2, **block(rows),
           "matching": block([r for r in rows if r[0].truth]),
           "non_matching": block([r for r in rows if not r[0].truth]),
           "both_twins_correct": _frac(sum(1 for a, b in zip(rows[::2], rows[1::2])
                                           if a[1] is a[0].truth and b[1] is b[0].truth), n // 2),
           "with_reasons": _frac(sum(1 for r in rows if r[2]), n),
           "by_kind": {kind: block([r for r in rows if r[0].kind == kind]) for kind in KINDS}}
    if detail:
        out["cases"] = [{"id": t.id, "truth": t.truth, "answer": a} for t, a, _ in rows]
    return out


def baseline() -> dict:
    return {split: {name: {"what": REFERENCES[name][0], **score(name, split)} for name in REFERENCES}
            for split in SPLITS}
