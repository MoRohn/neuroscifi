"""The form planner with real content (integration lane, item 4): `nsf form plan --content FILE...` hands the planner
the shapes of what the REAL adapters decode (each representation's `shape()`, and an archive's members' shapes), and
a form is feasible exactly when `make.catalog()` lists its generator. One test per family that the shape reaching the
planner is the sensible one; files are built by hand here."""
import io
import json
import struct
import zipfile

import pytest

from neuroscifi import cli
from neuroscifi.artifacts import forms, make
from neuroscifi.artifacts.forms_cli import shapes_from_files

from test_integration_store import FAMILIES
from test_integration_words import jpeg_with_exif

# family -> what the planner must be told about it (kinds that must be present, and the flags)
SENSIBLE = {
    "text": ({"prose"}, {"has_time": False, "has_space": 0}),
    "table": ({"series", "records"}, {"has_time": True}),            # dated numbers: a series in time
    "document": ({"prose"}, {}),
    "image": ({"image"}, {"has_space": 2}),
    "audio": ({"sound"}, {"has_time": True}),
    "mesh": ({"solid"}, {"has_space": 3}),                            # a closed tetrahedron
    "geo": ({"map"}, {"has_space": 2, "has_time": False}),
    "tree": ({"hierarchy"}, {}),
    "code": ({"network"}, {}),
    "array": ({"records"}, {}),
}


def _write(tmp_path, name, data):
    path = tmp_path / name
    path.write_bytes(data)
    return path


@pytest.mark.parametrize("family", list(SENSIBLE))
def test_the_shape_reaching_the_planner_is_the_sensible_one(tmp_path, family):
    name, data, _, _ = FAMILIES[family]
    (shape,), notes = shapes_from_files([_write(tmp_path, name, data)])
    kinds, flags = SENSIBLE[family]
    assert kinds <= set(shape.kinds), (family, shape)
    for key, value in flags.items():
        assert getattr(shape, key) == value, (family, key, shape)
    assert notes == []


def test_an_archive_hands_over_its_members_and_words_carried_by_a_picture_count(tmp_path):
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        z.writestr("rain.csv", FAMILIES["table"][1])
        z.writestr("note.txt", b"a few words\n")
    shapes, _ = shapes_from_files([_write(tmp_path, "both.zip", out.getvalue())])
    assert shapes[0].kinds == ("hierarchy",) and any("series" in s.kinds for s in shapes[1:])
    assert any("prose" in s.kinds for s in shapes[1:])
    (picture,), _ = shapes_from_files([_write(tmp_path, "p.png", FAMILIES["image"][1])])
    assert picture.text_chars > 0                                      # its tEXt words are counted


def test_a_track_with_timestamps_is_also_a_timeline(tmp_path):
    gpx = (b'<?xml version="1.0"?><gpx version="1.1" xmlns="http://www.topografix.com/GPX/1/1"><trk><trkseg>'
           b'<trkpt lat="50.0" lon="10.0"><time>2024-01-01T10:00:00Z</time></trkpt>'
           b'<trkpt lat="50.1" lon="10.1"><time>2024-01-01T10:05:00Z</time></trkpt></trkseg></trk></gpx>')
    (shape,), _ = shapes_from_files([_write(tmp_path, "walk.gpx", gpx)])
    assert {"map", "timeline"} <= set(shape.kinds) and shape.has_time


def test_midi_notes_are_a_sequence_in_time(tmp_path):
    def vlq(n):
        out = [n & 0x7F]
        while n > 0x7F:
            n >>= 7
            out.insert(0, (n & 0x7F) | 0x80)
        return bytes(out)
    events = b"".join(vlq(0) + bytes((0x90, 60 + i, 64)) + vlq(480) + bytes((0x80, 60 + i, 0)) for i in range(4))
    track = events + vlq(0) + b"\xff\x2f\x00"
    midi = b"MThd" + struct.pack(">IHHH", 6, 0, 1, 480) + b"MTrk" + struct.pack(">I", len(track)) + track
    (shape,), _ = shapes_from_files([_write(tmp_path, "tune.mid", midi)])
    assert {"sequence", "timeline"} <= set(shape.kinds) and shape.has_time and shape.duration_s > 0


def test_a_header_only_file_is_planned_as_words_with_a_note(tmp_path):
    shapes, notes = shapes_from_files([_write(tmp_path, "j.jpg", jpeg_with_exif())])
    assert shapes[0].kinds == () and "only its header" in notes[0]


def test_feasibility_is_the_generator_catalog_of_this_host():
    made_here = {row["form"] for row in make.catalog() if row["available"]}
    plan = forms.plan(forms.Request("show me how the rain changed"),
                      (forms.ContentShape(("series",), 365, 1, True, 0, False, 0.0, 0),), forms.Context())
    for candidate in plan.candidates:
        needs = {forms.FORMS[f].generator for f in candidate.forms if forms.FORMS[f].generator}
        if needs - made_here:
            assert not candidate.admitted, candidate.forms
    assert not ({forms.FORMS[f].generator for f in plan.chosen} - {None}) - made_here


def test_the_command_plans_from_files(tmp_path, capsys):
    path = _write(tmp_path, "rain.csv", FAMILIES["table"][1])
    assert cli.main(["form", "plan", "--request", "what is the wettest day?", "--content", str(path), "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["chosen"] and out["input_notes"] == []
    assert cli.main(["form", "plan", "--request", "x", "--content", str(tmp_path / "absent.csv")]) == 2
