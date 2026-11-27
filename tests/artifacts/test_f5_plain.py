"""Fix lane F5: what the owner is told on the terminal is true and plain (code review N3, N4, N2, L3; security
N-new-2). Item 1 (N3): a made thing read from part of a source, or from a reduction, says so in the summary and in
the "why" with the same numbers as its description, never "100% of the source values"; the row count of a stopped
CSV is the table's own."""
from __future__ import annotations

import io
import struct
import zipfile
from dataclasses import replace

import numpy as np

from neuroscifi.artifacts import cli
from neuroscifi.artifacts import produce as P
from neuroscifi.artifacts import produce_builders as B
from neuroscifi.artifacts.adapters import archives, tables
from neuroscifi.artifacts.detect import detect
from neuroscifi.artifacts.types import Limits

SMALL = Limits(max_decoded_bytes=1 << 20)


def _rows(n: int) -> bytes:
    return ("t,value\n" + "".join(f"{i},{(i * 37) % 101}\n" for i in range(n))).encode()


def _summary(production) -> str:
    return cli._plain("make", {"status": production.status, "artifact": "artifact-x", "kept": [],
                               "sources_kept": [], "words": list(production.words), "record": production.record})


def test_a_stopped_csv_counts_the_tables_own_rows_and_the_files_lines():
    data = _rows(20_000)
    decoded = tables.read_delimited(data, detect(data, name="rows.csv"), SMALL)
    assert decoded.partial
    n = decoded.representation.n_rows
    stop = [note for note in decoded.notes if note.startswith("stopped after")]
    assert stop == [f"stopped after {n} rows and the header line, at line {n + 1} of the file's 20001: the element "
                    f"or byte limit was reached; the {20_000 - n} lines after it were not read"]


def test_the_summary_says_a_partial_read_with_the_descriptions_numbers():
    made = P.produce(None, "show the value over t", [P.Given("rows.csv", _rows(20_000))], limits=SMALL,
                     partial_ok=True)
    assert made.status == "made", made.record.get("candidates")
    part = next(s["part"] for s in made.record["sources"] if s.get("used"))
    assert f"Only part of rows.csv was read: {part}." in made.spec.description
    text = _summary(made)
    assert f"Only part of rows.csv was used (--partial): {part}." in text
    assert "of the source values" not in text
    assert "of the values it was made from were read back from the file (they are the part that was read" in text
    why = P.why_text(made.record)
    assert f"Only part of rows.csv was used (--partial): {part}." in why and "of the source values" not in why


def test_a_whole_source_keeps_the_old_words():
    made = P.produce(None, "show the value over t", [P.Given("rows.csv", _rows(50))])
    assert made.status == "made"
    text = _summary(made)
    assert "Only part" not in text and "of the source values were read back from the file" in text


def test_the_solid_viewer_says_its_points_are_a_reduction(monkeypatch):
    monkeypatch.setattr(B, "MAX_SOLID_VIEW_FACES", 300)
    monkeypatch.setattr(B, "MAX_SOLID_VIEW_VERTICES", 600)
    tris = np.random.default_rng(4).normal(0, 5, (2000, 3, 3)).astype(np.float32)
    rec = np.zeros(len(tris), dtype=[("n", "<f4", 3), ("v", "<f4", (3, 3)), ("a", "<u2")])
    rec["v"] = tris
    stl = b"binary".ljust(80, b" ") + struct.pack("<I", len(tris)) + rec.tobytes()
    made = P.produce(None, "show me the shape", [P.Given("many.stl", stl)])
    assert made.status == "made", made.record.get("candidates")
    reduced = next(s["reduced"] for s in made.record["sources"] if s.get("used"))
    assert "points the view shows are a reduction of its 6000 vertices" in reduced
    assert f"many.stl was {reduced}." in made.spec.description
    text = _summary(made)
    assert f"many.stl was {reduced}." in text and "of the source values" not in text


def test_a_cut_zip_says_how_many_members_it_declares_and_how_many_were_not_read():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for i in range(30):
            z.writestr(f"n/{i:02d}.txt", f"note {i}\n")
    data = buf.getvalue()
    decoded = archives.read_zip(data, detect(data, name="notes.zip"), Limits(max_members=20))
    assert decoded.partial
    assert "stopped after the first 20 of the 30 members the archive declares (the limit is 20): the other 10 are " \
           "not read" in decoded.notes
    assert "the other 10 are not read" in P.part_read(decoded)


# Item 2 (code N4): `make --partial` on an archive of thousands of files makes a bounded tree of what was read


def _many(folders: int, per: int) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for f in range(folders):
            for i in range(per):
                z.writestr(f"notes/{f:02d}/note_{f * per + i:04d}.txt", f"note {i}\n")
    return buf.getvalue()


def test_an_archive_too_large_to_draw_member_by_member_is_drawn_as_its_folders():
    made = P.produce(None, "what is in this archive", [P.Given("notes.zip", _many(40, 30))],
                     limits=Limits(max_members=1000), partial_ok=True)
    assert made.status == "made", made.record.get("candidates")
    assert made.spec.spec == "graph.tree"
    labels = [n["label"] for n in made.spec.data["nodes"]]
    assert "00/ (30 files)" in labels and not any(label.startswith("note_") for label in labels)
    assert "only its folders are shown, each with how many files it holds directly" in made.spec.description
    assert "stopped after the first 1000 of the 1200 members the archive declares" in made.spec.description
    assert "the other 200 are not read" in _summary(made)


def test_a_small_archive_is_still_drawn_member_by_member():
    made = P.produce(None, "what is in this archive", [P.Given("notes.zip", _many(2, 3))], form="tree_diagram")
    assert made.status == "made"
    assert "note_0000.txt" in [n["label"] for n in made.spec.data["nodes"]]



# Item 3 (code N2): `make` exits with the code the outcome table gives, for every refusal

import pytest  # noqa: E402
from test_f4_forget_hold import T0, held_one  # noqa: E402,F401  (a fixture)

from neuroscifi import cli as top  # noqa: E402
from neuroscifi.artifacts import outcome  # noqa: E402
from neuroscifi.artifacts import store as A  # noqa: E402
from neuroscifi.instance import store as instance_store  # noqa: E402


@pytest.fixture
def individual(tmp_path):
    """An individual with every adapter here (held_one's own individual reads text and JSON only)."""
    path = tmp_path / "individual.sqlite"
    db = instance_store.create(path, T0)
    try:
        yield db, path
    finally:
        db.close()


def test_make_exits_with_the_outcome_tables_code(held_one, tmp_path, monkeypatch, capsys):  # noqa: F811
    _, path, unchecked, _, _ = held_one
    real = A.limits
    monkeypatch.setattr(A, "limits", lambda cfg: replace(real(cfg), max_decoded_bytes=SMALL.max_decoded_bytes))
    wide = ",".join(f"c{i}" for i in range(16_385)) + "\n" + ",".join("1" * 16_385) + "\n"   # over MAX_COLUMNS
    files = {"rows.csv": _rows(20_000), "wide.csv": wide.encode(), "x.bin": bytes(range(256)) * 64}
    for name, data in files.items():
        (tmp_path / name).write_bytes(data)
    cases = [  # (sources, extra arguments, the table's outcome)
        ([str(tmp_path / "rows.csv")], [], outcome.REFUSED),             # a part, and the owner did not agree
        ([str(tmp_path / "rows.csv")], ["--partial"], outcome.ANSWERED),
        ([str(tmp_path / "wide.csv")], [], outcome.REFUSED),            # a limit
        ([unchecked.id], [], outcome.REFUSED),                           # a held artifact
        ([str(tmp_path / "x.bin")], [], outcome.CANNOT),                 # nothing here reads its format
        ([str(tmp_path / "x.bin"), str(tmp_path / "wide.csv")], [], outcome.REFUSED),
    ]
    for sources, extra, expected in cases:
        rc = top.main(["artifact", "make", *sources, "-r", "show the value over t", "--db", str(path), *extra])
        out = capsys.readouterr().out
        assert rc == outcome.exit_code(expected), (sources, extra, out)
    assert outcome.of_production("failed", {}) == outcome.CANNOT
