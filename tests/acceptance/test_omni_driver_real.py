"""The `artifacts` driver (acceptance/omni/subject.py) against the REAL package (integration lane, item 5): how it
reads answers off the representations lanes K, B1, B2 and G2 actually return. Each input is built here by hand
(struct, zlib, zipfile, tarfile, numpy's own writer), not taken from the benchmark's tables."""
import io
import json
import struct
import tarfile
import zipfile

import numpy as np
import pytest

from neuroscifi.acceptance.omni import Task, forms, made, readers, subject


@pytest.fixture(scope="module")
def artifacts():
    s = subject.ArtifactsSubject()
    assert s.available() == (True, "")
    return s


def ask(subj, files, question, declared=None):
    task = Task("test/" + question, "test", files, declared or {}, question, None)
    return subj.answer(task)


def _zip(members, *, stored=False) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_STORED if stored else zipfile.ZIP_DEFLATED) as z:
        for name, data in members:
            z.writestr(zipfile.ZipInfo(name, (2020, 1, 1, 0, 0, 0)), data)
    return out.getvalue()


def _jpeg(width=3, height=2) -> bytes:
    sof = b"\xff\xc0" + struct.pack(">HBHHB", 17, 8, height, width, 3) + b"\x01\x11\x00\x02\x11\x00\x03\x11\x00"
    return b"\xff\xd8" + sof + b"\xff\xd9"


def test_a_header_only_format_answers_what_its_header_states_and_nothing_else(artifacts):
    files = {"p.jpg": _jpeg(3, 2)}
    assert ask(artifacts, files, "detect.format").value == "jpeg"
    assert ask(artifacts, files, "image.size").value == [3, 2]
    assert ask(artifacts, files, "image.mean_gray").status == "cannot"          # pixels are not decoded here


def test_detection_is_answered_when_the_content_cannot_be_read(artifacts):
    png_head = b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + b"\x00\x00\x00\x04"   # cut inside IHDR
    assert ask(artifacts, {"cut.png": png_head}, "detect.format").value == "png"
    assert ask(artifacts, {"cut.png": png_head}, "image.size").status == "refused"


def test_a_partial_decode_is_refused_not_counted(artifacts):
    flood = _zip([(f"m{i}.txt", b"x") for i in range(4200)], stored=True)        # past max_members (4096)
    answer = ask(artifacts, {"flood.zip": flood}, "bundle.n_members")
    assert answer.status == "refused" and answer.reason.startswith("partial")


def test_a_member_the_product_refused_is_a_refusal(artifacts):
    deep = b"a,b\n1,2\n"
    for level in range(6):
        deep = _zip([(f"l{level}.zip" if level else "deep.csv", deep)])
    question = "::".join(f"l{k}.zip" for k in range(4, 0, -1)) + "::deep.csv::table.n_rows"
    answer = ask(artifacts, {"deep.zip": deep}, question)
    assert answer.status == "refused" and "max_depth" in answer.reason


def test_a_climbing_member_name_is_never_taken_for_the_plain_name():
    rep = type("Bundle", (), {})()
    member = type("M", (), {"path": "../notes.txt", "inner": object(), "note": "", "detected": None})()
    rep.members = (member,)
    with pytest.raises(subject.Cannot):
        subject._member(rep, "notes.txt")


def test_npz_arrays_by_name_and_a_tar_gz_by_its_archive(artifacts):
    buf = io.BytesIO()
    np.savez(buf, pressure=np.array([1.5, 2.5, 3.0]), flow=np.arange(6, dtype=np.int32).reshape(2, 3))
    files = {"a.npz": buf.getvalue()}
    assert ask(artifacts, files, "pressure::array.shape").value == [3]
    assert ask(artifacts, files, "flow::array.shape").value == [2, 3]
    assert ask(artifacts, files, "flow::array.sum").value == 15.0
    assert ask(artifacts, files, "flow::array.dtype").status == "cannot"     # a Table does not carry the dtype
    tar = io.BytesIO()
    with tarfile.open(fileobj=tar, mode="w:gz", format=tarfile.USTAR_FORMAT) as t:
        for name in ("b.txt", "a/c.txt"):
            info = tarfile.TarInfo(name)
            info.size = 2
            t.addfile(info, io.BytesIO(b"x\n"))
    assert ask(artifacts, {"t.tar.gz": tar.getvalue()}, "bundle.members").value == ["a/c.txt", "b.txt"]


def test_code_imports_name_the_modules_statements_name(artifacts):
    src = b"import math\nfrom tide.shore import level\n\ndef f(x):\n    return level(x)\n"
    assert ask(artifacts, {"m.py": src}, "code.imports").value == ["math", "shore"]


def test_a_polyglot_is_refused_through_the_driver(artifacts):
    pdf = b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\ntrailer\n<< /Root 1 0 R >>\n%%EOF\n"
    answer = ask(artifacts, {"x.pdf": pdf + _zip([("a.txt", b"other\n")])}, "doc.contains:other")
    assert answer.status == "refused" and "also a ZIP archive" in answer.reason


def test_made_forms_go_through_the_real_generators_and_the_benchmarks_readers(artifacts):
    tone = {"form": "tone", "title": "A", "frequency_hz": 440.0, "duration_s": 0.5}
    a = artifacts.answer(Task("t/tone", "made", {"request.json": json.dumps(tone).encode()}, {}, "make:tone",
                              None, None, "make"))
    assert a.status == "answered" and a.value["media_type"] == "audio/wav"
    s = readers.read_wav(a.value["bytes"])
    assert abs(readers.dominant_hz(s.samples, s.rate) - 440.0) < 4.4
    mesh = {"form": "mesh", "title": "B", "format": "stl", "vertices": [[0, 0, 0]], "faces": []}
    a = artifacts.answer(Task("t/mesh", "made", {"request.json": json.dumps(mesh).encode()}, {}, "make:mesh", None,
                              None, "make"))
    assert a.status == "cannot"                      # no product form makes a solid from given vertices and faces
    produced = made.score(artifacts, "development")
    assert produced["wrong"] == 0 and produced["failed"] == 0


def test_the_planner_is_a_subject_of_the_forms_family():
    out = forms.score(subject.choose_form, "development")
    assert out["n_cases"] == 133 and out["hard_constraints_violated"]["k"] == 0
    assert subject.FORMS_UNMAPPED == [] or all(kind != "explicit" for kind, _ in subject.FORMS_UNMAPPED)
