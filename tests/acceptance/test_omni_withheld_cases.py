"""The OMNIMIND benchmark's WITHHELD split (lane ZW): the integrity and the independence of its cases.

DO NOT OPEN THIS FILE unless you are lane ZW (it names the shapes the withheld cases take). It imports the withheld
modules to check them and prints nothing about them: every assertion message is an id or a count.

forms: the examiner's judgements are well formed, no good or fine form breaks a hard constraint, the explicit-request
rules hold, the pairs share their cue words and the twins their content and context, both with disjoint good sets; the
counts the brief sets bounds on; and no request repeats or nearly repeats a development or held-out request.
Lane Z's families: seeds from the reserved range, new names, every question in the measurement vocabulary, and every
truth re-derived by the independent oracle (tests/acceptance/omni_oracle.py), the header parsers and readers of
test_omni_families.py, the reference maker and the reference transforms; hostile files are what their attack says.
cross: every truth re-derived from the bytes (test_omni_cross_bench.py's readers for the six earlier kinds, readers
written here for the new ones), the twins differing in exactly one value. learn: SIMULATED people whose profiles no
earlier person has, and a family on which the reference learner can show learning."""
from collections import Counter, defaultdict
import gzip
import hashlib
import io
import json
import re
import struct
import tarfile
import wave
import xml.etree.ElementTree as ET
import zipfile
import zlib

import numpy as np
import pytest

from neuroscifi.acceptance import omni
from neuroscifi.acceptance.omni import (cases, cross, cross_cases, forms, forms_cases, learn, learn_people, made,
                                        subject, transform)
from neuroscifi.acceptance.omni import withheld_cases as W
from neuroscifi.acceptance.omni import withheld_cross as WX
from neuroscifi.acceptance.omni import withheld_forms as WF
from neuroscifi.acceptance.omni import withheld_learn as WL

import omni_oracle as oracle
import omni_reference_maker as reference
import test_omni_cross_bench as cross_readers
import test_omni_families as fam

KINDS = {"scalar", "series", "categories", "distribution", "relation", "hierarchy", "network", "sequence", "timeline",
         "process", "surface", "solid", "map", "image", "sound", "prose", "code", "records"}
OLD_FORMS = [c for split in forms.SPLITS for c in forms_cases.ALL[split]]
# The generated identity of the withheld split, pinned with the files' SHA-256 (test_omni_final.py): a numpy or
# generator change that alters a byte fails here and must be reported, not re-pinned silently.
FROZEN = {
    "intake": {"n": 358, "sha256": "cf8d7b035c25d88391be21481905fb6e9784bf1ae540fd7a64f7c5f190bed7fc"},
    "honest": {"n": 55, "sha256": "2f100a6c8f900d29b06ea08419ed60e305cf0061449e7558406f2c6397b3cad1"},
    "unknown": {"n": 111, "sha256": "483e42865b2af633b7e3366363119eecaf295342e72737a4af92dfad59101726"},
    "compound": {"n": 66, "sha256": "9c5638b5744ed6f376d525a5263fd6da700f01e12743913ccc4cce88c39ce308"},
    "hostile": {"n": 35, "sha256": "f6e1f516dabd653add3648b1eb223cfd60106008762e5b9cccaaf5f797025aa3"},
    "made": {"n": 16, "sha256": "bcc300c179305c4a78245bc3c8cf95b6ce4a126feccac96f3371290864d35013"},
    "transform": {"n": 11, "sha256": "08995bd2a492ce3e30a25dbdd1f9fa058dfbac48a975217042932f09a43d5ef8"},
}
CROSS_GENERATED_SHA256 = "47e8c95fdf6b38ea054ed25893ebaba8ddd303a1f128547e2e51c6e1e5c796ff"


# ---- forms ------------------------------------------------------------------------------------------------------------

def test_forms_judgements_are_well_formed_and_feasible():
    ids = [c["id"] for c in WF.WITHHELD]
    assert len(ids) == len(set(ids)) and not set(ids) & {c["id"] for c in OLD_FORMS}
    for c in WF.WITHHELD:
        assert {k for s in c["content"] for k in s["kinds"]} <= KINDS, c["id"]
        assert c["sets"]["good"]["forms"], c["id"]
        listed = []
        for name, entry in c["sets"].items():
            assert bool(entry["forms"]) == bool(entry["reason"]) or (not entry["forms"] and c["rest"] == name), \
                (c["id"], name)
            for f in entry["forms"]:
                assert all(p in forms.FORMS for p in forms.norm(f).split("+")), c["id"]
                listed.append(forms.norm(f))
        assert len(listed) == len(set(listed)), c["id"]
        assert len([t for t in c["tags"] if t in ("content", "words", "conflict", "explicit")]) == 1, c["id"]
        assert ("explicit" in c["tags"]) == bool(c["explicit"]), c["id"]
        for name in ("good", "fine"):
            for f in c["sets"][name]["forms"]:
                assert forms.violation(f, c) is None, (c["id"], name)
        if c["explicit"]:
            good_parts = {p for f in c["sets"]["good"]["forms"] for p in forms.norm(f).split("+")}
            if forms.explicit_feasible(c):
                assert c["explicit"] in good_parts, c["id"]
            else:
                assert c["explicit"] not in {forms.norm(f) for f in c["sets"]["good"]["forms"]}, c["id"]
                assert forms.label(c, c["explicit"])[0] == "violation", c["id"]


def test_forms_pairs_and_twins():
    pairs, twins = defaultdict(list), defaultdict(list)
    for c in WF.WITHHELD:
        if c["pair"]:
            pairs[c["pair"][0]].append(c)
        if c["twin"]:
            twins[c["twin"]].append(c)
    assert len(pairs) >= 40 and len(twins) >= 25
    for pid, (a, b) in pairs.items():
        assert a["pair"][1] == b["pair"][1] and all(a["pair"][1].lower() in x["request"].lower() for x in (a, b)), pid
        assert a["content"] != b["content"], pid
        assert not {forms.norm(f) for f in a["sets"]["good"]["forms"]} & \
            {forms.norm(f) for f in b["sets"]["good"]["forms"]}, pid
    for tid, (a, b) in twins.items():
        assert a["content"] == b["content"] and a["context"] == b["context"], tid
        assert not {forms.norm(f) for f in a["sets"]["good"]["forms"]} & \
            {forms.norm(f) for f in b["sets"]["good"]["forms"]}, tid
    old_cues = {c["pair"][1].lower() for c in OLD_FORMS if c["pair"]}
    assert not {m[0]["pair"][1].lower() for m in pairs.values()} & old_cues


def test_forms_counts_the_brief_sets_bounds_on():
    cs = WF.WITHHELD
    assert len(cs) >= 150
    assert sum(forms.prose_right(c) for c in cs) * 3 >= len(cs)
    assert {k for c in cs for s in c["content"] for k in s["kinds"]} == KINDS
    assert {c["context"]["device"] for c in cs} == {"console", "pocket", "voice", "cli", "agent"}
    explicit = [c for c in cs if c["explicit"]]
    assert sum(not forms.explicit_feasible(c) for c in explicit) >= 4 and sum("less" in c["tags"] for c in cs) >= 3
    assert {a for c in cs for a in c["context"]["access"]} == {"screen_reader", "voice_only", "reduced_motion",
                                                               "no_sound"}
    # budgets or access needs that make the content's obvious form (the shape-only reference's) a violation
    blocked = [c for c in cs if ("budget" in c["tags"] or "access" in c["tags"])
               and forms.violation(forms.SHAPE_ONLY[c["content"][0]["kinds"][0]], c)]
    assert len(blocked) >= 30
    old_purposes = {c["purpose"] for c in OLD_FORMS if c["purpose"]}
    assert sum(1 for c in cs if c["purpose"] and c["purpose"] not in old_purposes) >= 15


def test_forms_requests_repeat_no_earlier_request():
    old = [c["request"] for c in OLD_FORMS]
    lowered = {r.lower() for r in old}
    digits = re.compile(r"\b\d[\d,.:]*\b")
    old_numbers = {n for r in old for n in digits.findall(r)}
    for c in WF.WITHHELD:
        assert c["request"].lower() not in lowered, c["id"]
        words = forms.tokens(c["request"])
        nearest = max(len(words & forms.tokens(r)) / max(1, len(words | forms.tokens(r))) for r in old)
        assert nearest < 0.4, c["id"]
        assert not set(digits.findall(c["request"])) & old_numbers, c["id"]
    requests = [c["request"].lower() for c in WF.WITHHELD]
    assert len(set(requests)) == len(requests)


def test_forms_no_frequent_word_decides_the_form():
    """For every word in at least six withheld requests, the largest share of them for which one same form is good
    (what the best rule "this word -> that form" would score) stays below 0.9, as forms.wording() asks of the earlier
    splits."""
    by_word = defaultdict(list)
    for c in WF.WITHHELD:
        for word in forms.tokens(c["request"]):
            by_word[word].append(c)
    shares = [Counter(f for c in m for f in forms._good_forms(c)).most_common(1)[0][1] / len(m)
              for m in by_word.values() if len(m) >= 6]
    assert shares and max(shares) < 0.9


def test_forms_view_hides_the_judgement():
    from neuroscifi.acceptance.omni import final
    for task in final.forms_tasks():
        assert set(task.view) == {"id", "request", "explicit", "purpose", "content", "context"}


# ---- lane Z's seven families ------------------------------------------------------------------------------------------

def test_seeds_names_and_vocabulary():
    seeds = [c["seed"] for table in W.TABLES.values() for c in table]
    assert len(seeds) == len(set(seeds)) and all(s in omni.WITHHELD_SEEDS for s in seeds)
    for name, table in W.TABLES.items():
        old = getattr(cases, name.upper())
        assert not {c["name"] for c in table} & {c["name"] for s in old for c in old[s]}, name
    measures = {m.split(":")[0] for m in subject.MEASURES}
    for family in W.FAMILIES:
        tasks = W.tasks(family)
        assert len({t.id for t in tasks}) == len(tasks), family
        for t in tasks:
            if t.kind not in ("make", "transform"):
                assert t.question.split("::")[-1].split(":")[0] in measures, t.id


def test_the_generated_split_is_frozen():
    got = {}
    for family in W.FAMILIES:
        tasks = W.tasks(family)
        got[family] = {"n": len(tasks),
                       "sha256": hashlib.sha256("\n".join(sorted(t.digest() for t in tasks)).encode()).hexdigest()}
    assert got == FROZEN


def _wav_any_width(data):
    with wave.open(io.BytesIO(data)) as w:
        if w.getsampwidth() != 4:
            return None
        n, ch, rate = w.getnframes(), w.getnchannels(), w.getframerate()
        x = np.frombuffer(w.readframes(n), "<i4").astype(float)
    return {"kind": "sound", "x": x.reshape(n, ch), "rate": rate}


def _oracle_answer(name, data, question):
    if name.endswith(".wav"):
        rep = _wav_any_width(data)
        if rep is not None:
            return oracle.sound_measure(rep, *question.partition(":")[::2])
    return oracle.answer(name, data, question)


def test_intake_truths_agree_with_the_oracle():
    checked = 0
    for t in W.tasks("intake"):
        if t.kind not in ("measure", "cannot_or_value"):
            continue
        (name, data), = t.files.items()
        try:
            value = _oracle_answer(name, data, t.question)
        except oracle.NotOracle:
            continue
        assert omni.same(t.expect, value, t.tolerance), t.id
        checked += 1
    assert checked >= 250


def test_honest_header_facts_agree_with_the_parsers():
    checked = 0
    for t in W.tasks("honest"):
        if t.kind not in ("measure", "cannot_or_value") or t.question.startswith("detect."):
            continue
        (name, data), = t.files.items()
        assert omni.same(t.expect, fam.honest_reading(name, data, t.question), t.tolerance), t.id
        checked += 1
    assert checked >= 20


_TRUE_NAME = dict(fam._TRUE_NAME, json_utf16be="x.json", npy_named_bin="x.npy", svg_bare="x.svg",
                  docx_named_zip="x.docx", png_zstd="x.png", json_records_gzip="x.json")


def _unwrap(make, data):
    if make == "json_utf16be":
        assert data[:2] == b"\xfe\xff"
        return data[2:].decode("utf-16-be").encode()
    if make == "json_records_gzip":
        return gzip.decompress(data)
    if make == "png_zstd":
        zstandard = pytest.importorskip("zstandard")
        return zstandard.ZstdDecompressor().decompress(data)
    return fam._unwrap(make, data)


def test_disguised_files_hold_what_their_tasks_say_and_unknown_bytes_are_unknown():
    by = {c["name"]: c for c in W.UNKNOWN}
    checked = 0
    for t in W.tasks("unknown"):
        case = by[t.id.split("/")[2]]
        (_, data), = t.files.items()
        if case["kind"] == "disguised":
            if t.question.startswith("detect."):
                continue
            value = oracle.answer(_TRUE_NAME[case["make"]], _unwrap(case["make"], data), t.question)
            assert omni.same(t.expect, value, t.tolerance), t.id
            checked += 1
        else:
            assert t.kind == "cannot" or t.question.startswith("detect."), t.id
            heads = {"head_of_png": b"\x89PNG", "head_of_zip": b"PK\x03\x04", "riff_unknown_form": b"RIFF",
                     "pdf_head_noise": b"%PDF"}
            if case["make"] in heads:
                assert data.startswith(heads[case["make"]]), t.id
            else:
                assert not data.startswith((b"\x89PNG", b"PK\x03\x04", b"\x1f\x8b", b"%PDF", b"GIF8", b"RIFF",
                                            b"\x28\xb5\x2f\xfd", b"\x93NUMPY")), t.id
                assert not zipfile.is_zipfile(io.BytesIO(data)), t.id
            if t.question == "detect.format" and case["make"] not in heads:
                assert t.expect == "unknown", t.id
    assert checked >= 40
    assert sum(c["kind"] == "disguised" for c in W.UNKNOWN) >= 20


def test_compound_truths_agree_with_an_independent_reading():
    checked = 0
    for t in W.tasks("compound"):
        (name, data), = t.files.items()
        try:
            value = fam._compound_reading(name, data, t.question)
        except oracle.NotOracle:
            continue
        assert omni.same(t.expect, value, t.tolerance), t.id
        checked += 1
    assert checked >= 60


def test_made_and_transform_are_right_for_the_reference_and_flaws_are_caught():
    maker = reference.ReferenceMaker()
    for t in W.tasks("made"):
        assert made.judge_made(t, maker.answer(t), maker.answer(t)).outcome == "right", t.id
    skewed = reference.ReferenceMaker("skew")
    charts = [t for t in W.tasks("made") if t.question.split(":")[1] in ("line", "bar", "scatter", "histogram")]
    assert charts and all(made.judge_made(t, skewed.answer(t), skewed.answer(t)).outcome == "wrong" for t in charts)
    for t in W.tasks("transform"):
        assert transform.judge(t, fam.reference_transform(t), fam.reference_transform(t)).outcome == "right", t.id


def _file(name):
    task = next(t for t, _ in W.hostile_tasks() if t.id.endswith("/" + name))
    return next(iter(task.files.values()))


def test_hostile_files_are_what_their_attack_says():
    tasks = W.hostile_tasks()
    assert all(t.kind == "refuse" for t, _ in tasks)
    old_attacks = {c["attack"] for s in cases.HOSTILE for c in cases.HOSTILE[s]}
    new = {c["attack"] for c in W.HOSTILE} - old_attacks
    assert set(W.NEW_HOSTILE_CLASSES) <= new and len(W.NEW_HOSTILE_CLASSES) >= 5
    assert all(len(next(iter(t.files.values()))) < 64 << 20 for t, _ in tasks)                # small on disk
    by = {c["attack"]: c["name"] for c in W.HOSTILE}
    z = zipfile.ZipFile(io.BytesIO(_file(by["xlsx_bomb"])))
    info = z.getinfo("xl/sharedStrings.xml")
    assert info.file_size == 1 << 30 and info.compress_size < 2 << 20
    with z.open(info) as fh:
        assert fh.read(1 << 20) == b" " * (1 << 20)
    pdf = _file(by["pdf_bomb"])
    start = pdf.index(b"stream\n") + 7
    assert zlib.decompressobj().decompress(pdf[start:start + 4096], 1 << 20) == b" " * (1 << 20)
    assert b"<!ENTITY" in _file(by["svg_laughs"])
    assert b"<!DOCTYPE" in zipfile.ZipFile(io.BytesIO(_file(by["docx_laughs"]))).read("word/document.xml")
    gif = _file(by["gif_huge"])
    assert struct.unpack("<HH", gif[6:10]) == (65535, 65535) and struct.unpack("<HHHH", gif[20:28])[2:] == (65535,
                                                                                                           65535)
    head = io.BytesIO(_file(by["npy_overflow"]))
    np.lib.format.read_magic(head)
    shape = np.lib.format.read_array_header_1_0(head)[0]
    assert int(np.prod(np.array(shape, dtype=np.int64))) == 0 and shape[0] * shape[1] == 1 << 64    # wraps to zero
    head = io.BytesIO(_file(by["npy_object"]))
    np.lib.format.read_magic(head)
    assert np.lib.format.read_array_header_1_0(head)[2] == np.dtype(object)
    assert zipfile.ZipFile(io.BytesIO(_file(by["zip_duplicate"]))).namelist() == ["data.csv", "data.csv"]
    with tarfile.open(fileobj=io.BytesIO(_file(by["tar_hardlink"]))) as tar:
        link = tar.getmember("data.csv")
        assert link.islnk() and link.linkname == "/etc/hostname"
    with tarfile.open(fileobj=io.BytesIO(_file(by["tar_pax_path"]))) as tar:
        assert any(m.name.startswith("../../") for m in tar.getmembers())                  # a reader that obeys PAX
    raw = _file(by["zip_unicode_path"])
    assert b"notes.txt" in raw and b"\x75\x70" in raw                                    # a plain name, an extra field
    assert any(n.startswith("../../") for n in zipfile.ZipFile(io.BytesIO(raw)).namelist())   # that names a path out
    assert len(_file(by["json_bignum"])) > 10_000_000
    assert b"/Kids [2 0 R" in _file(by["pdf_cycle"])
    assert b"..\\..\\" in _file(by["zip_backslash"])
    escapes = [e for _, e in tasks if e]
    assert len(escapes) == 4 and all(".." in p for e in escapes for p in e)


def test_isolation_runs_the_withheld_hostile_tasks(tmp_path):
    from neuroscifi.acceptance.omni import hostile, isolate
    for task, escapes in W.hostile_tasks()[-8:]:
        report = isolate.run_isolated(task, "probe:refuse", tmp_path, seconds=30, escape_paths=escapes)
        assert hostile.judge(task, report).outcome == "right", task.id


# ---- cross ------------------------------------------------------------------------------------------------------------

def _png_gray_pixels(data):
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    pos, idat, size = 8, b"", None
    while pos < len(data):
        n, kind = struct.unpack(">I4s", data[pos:pos + 8])
        body = data[pos + 8:pos + 8 + n]
        if kind == b"IHDR":
            size = struct.unpack(">II", body[:8])
        elif kind == b"IDAT":
            idat += body
        pos += 12 + n
    w, h = size
    raw = zlib.decompress(idat)
    rows = [raw[y * (w + 1):(y + 1) * (w + 1)] for y in range(h)]
    assert all(r[0] == 0 for r in rows)
    return np.array([list(r[1:]) for r in rows], dtype=np.uint8)


def _count_squares(px):
    dark = px < 128
    seen, count = np.zeros_like(dark), 0
    for y, x in zip(*np.nonzero(dark)):
        if seen[y, x]:
            continue
        count += 1
        stack = [(y, x)]
        while stack:
            a, b = stack.pop()
            if 0 <= a < dark.shape[0] and 0 <= b < dark.shape[1] and dark[a, b] and not seen[a, b]:
                seen[a, b] = True
                stack += [(a + 1, b), (a - 1, b), (a, b + 1), (a, b - 1)]
    return count


def _new_kind_values(pair, files):
    k = pair["knobs"]
    if pair["kind"] == "image_caption":
        px = _png_gray_pixels(files[k["png"]])
        stated = re.search(r"shows (\d+) black squares on a white field of (\d+) by (\d+)",
                           files[k["doc"]].decode())
        assert (int(stated.group(2)), int(stated.group(3))) == (px.shape[1], px.shape[0])
        return _count_squares(px), int(stated.group(1))
    if pair["kind"] == "route_stops":
        root = ET.fromstring(files[k["gpx"]])
        stops = len([e for e in root if e.tag.endswith("wpt")])
        return stops, int(re.search(r"made (\d+) stops", files[k["note"]].decode()).group(1))
    z = zipfile.ZipFile(io.BytesIO(files[k["zip"]]))
    actual = {i.filename: i.file_size for i in z.infolist()}
    text = files[k["manifest"]].decode()
    if k["fmt"] == "json":
        listed = {e["path"]: e["bytes"] for e in json.loads(text)["files"]}
    else:
        listed = {p: int(s) for s, p in (line.split() for line in text.splitlines() if line.strip())}
    return actual, listed


def test_cross_truths_from_the_bytes_and_twins_differ_in_one_value():
    pairs = {p["id"]: p for p in WX.PAIRS}
    assert len(pairs) >= 36 and not set(pairs) & {p["id"] for s in cross_cases.ALL for p in cross_cases.ALL[s]}
    assert len(set(WX.NEW_KINDS) - set(cross.KINDS)) >= 2
    assert {p["kind"] for p in WX.PAIRS} == set(cross.KINDS) | set(WX.NEW_KINDS)
    tasks = WX.tasks()
    for match, other in zip(tasks[::2], tasks[1::2]):
        pair = pairs[match.id[:-1]]
        assert match.truth is True and other.truth is False and match.question == other.question
        if pair["kind"] in cross.KINDS:
            assert cross_readers.truth_from_bytes(pair, match.files) is True, match.id
            assert cross_readers.truth_from_bytes(pair, other.files) is False, other.id
            continue
        a, b = _new_kind_values(pair, match.files), _new_kind_values(pair, other.files)
        assert a[0] == a[1] and b[0] != b[1], match.id
        if isinstance(a[0], dict):
            changed = [p for p in a[0] if a[0][p] != b[0][p]] + [p for p in a[1] if a[1][p] != b[1][p]]
            assert len(changed) == 1 and set(a[0]) == set(b[0]) == set(a[1]) == set(b[1]), match.id
        else:
            assert (a[0] != b[0]) + (a[1] != b[1]) == 1 and abs(a[0] - b[0]) + abs(a[1] - b[1]) == 1, match.id
    assert WX.digest() == WX.digest() == CROSS_GENERATED_SHA256


def test_cross_references_score_one_half():
    from neuroscifi.acceptance.omni import final
    for name, share in (("always_consistent", 0.5), ("always_inconsistent", 0.5), ("today", 0.0)):
        assert final.score_cross(name)["correct"]["share"] == share


# ---- learn ------------------------------------------------------------------------------------------------------------

def test_learn_people_are_new_and_simulated():
    assert "SIMULATED" in WL.__doc__
    old = [p for s in learn_people.ALL for p in learn_people.ALL[s]]
    old_maps = {json.dumps(learn.profile(p), sort_keys=True) for p in old}
    assert len(WL.PEOPLE) >= 6
    for p in WL.PEOPLE:
        assert json.dumps(learn.profile(p), sort_keys=True) not in old_maps, p["id"]
    assert not {p["id"] for p in WL.PEOPLE} & {p["id"] for p in old}
    assert all(s in omni.WITHHELD_SEEDS for s in WL.SEEDS) and len(WL.SEEDS) == 10
    assert not set(WL.SEEDS) & {s for v in learn_people.SEEDS.values() for s in v}
    habits = {(p["noise"], p["replace"], p["accept_second"]) for p in old}
    assert sum((p["noise"], p["replace"], p["accept_second"]) not in habits for p in WL.PEOPLE) >= 3


def test_learn_the_reference_learner_can_show_learning_here():
    from neuroscifi.acceptance.omni import final
    r = {name: final.score_learn(name) for name in ("never_learns", "key_majority", "oracle")}
    late = {k: v["late_kept"]["mean"] for k, v in r.items()}
    assert late["oracle"] >= late["key_majority"]
    assert late["key_majority"] - late["never_learns"] > 2 * 2 ** 0.5 * r["never_learns"]["late_kept"]["sd_over_seeds"]
    assert Counter(r["oracle"]["late_kept_by_person"]) and r["oracle"]["denominator_per_cycle"] == 600
