"""Lane R (ADR-077): relating artifacts by measurement. The files are built by hand in r_pairs.py (standard library),
the truths are known by construction, and every expected value here is computed independently of relate.py."""
import json
import math

import numpy as np
import pytest

import r_pairs as RP
from neuroscifi.artifacts import adapters, relate as R, relate_read as rr
from neuroscifi.artifacts import types as T


def side(name, data):
    return R.side(adapters.decode(data, name=name), name=name, data=data)


def run(files, question=None):
    (na, da), (nb, db) = list(files.items())
    return R.compare(side(na, da), side(nb, db), question)


# ---- the reader of statements ------------------------------------------------------------------------------------

@pytest.mark.parametrize("text, values", [
    ("It came to 1,234.56 in all.", [1234.56]),
    ("Es waren 1.234,56 EUR und 12,5 kg.", [1234.56, 12.5]),
    ("Grand total: 16874.05", [16874.05]),
    ("about 13,000 people", [13000.0]),
    ("1 234,5 m", [1234.5]),
])
def test_numbers_in_their_formats(text, values):
    assert [n.value for n in rr.numbers(text, rr.convention(text))] == pytest.approx(values)


def test_dates_times_versions_and_glued_digits_are_not_quantities():
    text = "Release 1.2.3 on 2026-04-22T18:34:23Z at 18:34, build x86 and v2."
    assert rr.numbers(text) == []


def test_an_ambiguous_grouping_keeps_both_readings_without_a_convention():
    n = rr.numbers("It weighs 1,234 units")[0]
    assert n.values == (1234.0, 1.234)
    assert rr.numbers("1,234 and 7.5", rr.convention("1,234 and 7.5"))[0].values == (1234.0,)


def test_statements_kinds_units_aggregates_and_tolerances():
    said = rr.statements_of_text(
        "The figures below cover 9 entries.\nTotal spend came to 12,808.58 euros.\n"
        "Overall size: 120 x 40 x 60 mm.\nBounding box (mm): x -42.5 to 42.5, y 0 to 25, z 0 to 30.\n"
        "The part is 200 mm wide, 150 mm deep and 3 mm tall.\nTemperatures ranged from 5 to 31 degrees Celsius.\n"
        "The average was about 20.\nIn all, 17718 was paid.")
    by = [(s.kind, s.value, s.unit.symbol if s.unit else None, s.aggregate, s.noun) for s in said]
    assert ("count", 9, None, "count", "entry") in by
    assert ("number", 12808.58, "EUR", "sum", None) in by
    assert ("extent", (120.0, 40.0, 60.0), "mm", None, None) in by
    assert ("box", {"x": (-42.5, 42.5), "y": (0.0, 25.0), "z": (0.0, 30.0)}, "mm", None, None) in by
    assert ("extent", (200.0, 150.0, 3.0), "mm", None, None) in by
    assert ("range", (5.0, 31.0), "degC", "range", None) in by
    assert ("number", 20.0, None, "mean", None) in by
    assert ("number", 17718.0, None, "sum", None) in by                # "was paid" is not what 17718 counts
    tol = {s.value: s.tolerance for s in said if s.kind == "number"}
    # "about 20": half a unit of its last non-zero digit (the tens), as it is wider than 5 %
    assert tol[12808.58] == pytest.approx(0.005) and tol[20.0] == pytest.approx(5.0)


def test_a_log_is_read_as_timed_events_and_prose_is_not():
    log = "Mar 19 13:35:56 gw r[4]: link down\nMar 19 13:36:35 gw r[4]: link up\n2026-01-02T03:04:05Z x\n"
    ev = rr.events_of_text(log)
    assert [e.words for e in ev][:2] == [("gw", "r", "4", "link", "down"), ("gw", "r", "4", "link", "up")]
    assert ev[0].absolute is False and ev[2].absolute is True
    assert rr.events_of_text("We met on 2026-01-02T03:04:05Z.\nIt rained.\nThen it stopped.\n") is None


def test_cleaned_text_has_no_control_or_bidirectional_characters():
    assert R.clean("a\x1b[31mb‮c\nd") == "a [31mb c d"


# ---- what representations measure -------------------------------------------------------------------------------

def test_table_aggregates_equal_an_independent_computation():
    data = RP.csv(["Item", "Cost (EUR)"], [("a", "1,500.25"), ("b", "20.5"), ("c", "300")])
    # quoted grouped numbers stay text in the table adapter; relate reads them as one consistent format
    data = data.replace(b"1,500.25", b'"1,500.25"')
    qs = {q.ref: q for q in R.quantities(adapters.decode(data, name="c.csv").representation)}
    values = [1500.25, 20.5, 300.0]
    assert qs["table.column[1].sum"].value == pytest.approx(sum(values))
    assert qs["table.column[1].mean"].value == pytest.approx(sum(values) / 3)
    assert qs["table.column[1].max"].value == 1500.25 and qs["table.column[1].sum"].unit.symbol == "EUR"
    assert qs["table.rows"].value == 3


def test_tones_are_counted_and_their_pitches_measured_from_the_samples():
    freqs = [262, 330, 392, 523, 440]
    qs = {q.ref: q for q in R.quantities(adapters.decode(RP.wav_tones(freqs), name="t.wav").representation)}
    assert qs["signal.tones"].value == 5
    pitches = [p for _, p in qs["signal.pitches"].value]
    assert all(abs(p - f) <= 0.01 * f for p, f in zip(pitches, freqs))


def test_noise_is_not_claimed_to_be_tones():
    rng = np.random.default_rng(0)
    sig = T.Signal((rng.uniform(-0.5, 0.5, (8000, 1))).astype(np.float32), 8000.0, ("mono",))
    assert R.tone_segments(sig) is None
    assert "signal.tones" not in {q.ref for q in R.quantities(sig)}


def test_mesh_size_uses_the_unit_its_own_comment_states():
    s = side("b.obj", RP.obj_box(2, 3, 4, unit_comment="centimetres"))
    size = next(f for f in s.facts if f.ref == "mesh.size")
    assert size.value == pytest.approx((2.0, 3.0, 4.0)) and size.unit.symbol == "cm"
    assert next(f for f in s.facts if f.ref == "mesh.volume").value == pytest.approx(24_000.0)   # mm3


def test_a_chart_is_read_through_its_marks_and_never_its_metadata():
    svg = RP.svg_bars(["Jan", "Feb", "Mar"], [10, 30, 20], "Visits", ymax=50)
    lying = svg.replace(b"<title>", b'<metadata>{"rows": [["Jan", 99], ["Feb", 99], ["Mar", 99]]}</metadata>'
                                     b"<title>", 1)
    for data in (svg, lying):
        series = next(f for f in R.statements(adapters.decode(data, name="c.svg").representation, data=data)
                      if f.kind == "series")
        assert [(x, round(y, 6)) for x, y in series.value] == [("Jan", 10.0), ("Feb", 30.0), ("Mar", 20.0)]


def test_code_and_trace_pairs():
    code = b"def main():\n    load()\n    run()\n\n\ndef load():\n    return 1\n\n\ndef run():\n    load()\n"
    g = next(q for q in R.quantities(adapters.decode(code, name="m.py").representation) if q.kind == "pairs"
             and q.get("edge") == "calls")
    assert set(g.value) >= {("main", "load"), ("main", "run"), ("run", "load")}
    tree = json.dumps({"function": "main", "calls": [{"function": "load", "calls": []},
                                                     {"function": "run", "calls": [{"function": "load"}]}]})
    st = R.statements(adapters.decode(tree.encode(), name="t.json").representation)
    assert sorted(next(f for f in st if f.kind == "pairs").value) == [("main", "load"), ("main", "run"),
                                                                       ("run", "load")]


# ---- comparing: kinds of pairing the public splits do not have --------------------------------------------------

@pytest.mark.parametrize("kind, question, matching, other", RP.pairs(), ids=[p[0] for p in RP.pairs()])
def test_own_pairs_matching_and_non_matching(kind, question, matching, other):
    assert run(matching, question).answer is True
    rel = run(other, question)
    assert rel.answer is False
    assert any(p.verdict == "inconsistent" for k, p in enumerate(rel.pairs) if k in rel.decided_by)


@pytest.mark.parametrize("question, files", RP.unrelated(), ids=[",".join(f) for _, f in RP.unrelated()])
def test_unrelated_artifacts_are_cannot(question, files):
    rel = run(files, question)
    assert rel.answer is None and rel.because()[0].startswith("cannot tell")


def test_a_question_about_a_total_is_not_answered_from_other_numbers():
    files = {"r.txt": b"The figures cover 3 entries.\nThe largest entry was 30.\nWe paid 70 overall, roughly.\n",
             "d.csv": RP.csv(["Item", "Amount"], [("a", 10), ("b", 30), ("c", 20)])}
    rel = run(files, "Does the total in the report match the CSV?")
    assert rel.answer is None                      # count and largest agree, but the total cannot be compared


def test_two_columns_that_fit_one_statement_leave_it_undecided():
    files = {"n.txt": b"The largest value was 30.\n",
             "d.csv": RP.csv(["Item", "Cost", "Tax"], [("a", 10, 30), ("b", 30, 3)])}
    assert run(files, "Is the largest value right?").answer is None


def test_agreement_is_not_claimed_while_a_statement_about_the_subject_is_uncompared():
    files = {"n.txt": b"The figures cover 3 entries.\nI worked 70 hours.\n",
             "t.csv": RP.csv(["Day", "Hours"], [("Mon", 10), ("Tue", 30), ("Wed", 20)])}
    assert run(files).answer is None
    assert run(files, "Do the hours add up to what the note says?").answer is False


def test_a_bound_and_an_approximation_are_honoured():
    data = RP.csv(["Item", "Amount"], [("a", 600), ("b", 650)])
    assert run({"n.txt": b"In total we paid more than 1,200.\n", "d.csv": data}, "total?").answer is True
    assert run({"n.txt": b"In total we paid more than 1,300.\n", "d.csv": data}, "total?").answer is False
    assert run({"n.txt": b"In total we paid about 1,200.\n", "d.csv": data}, "total?").answer is True


def test_a_mesh_without_a_unit_is_not_judged_by_a_scale_it_does_not_state():
    files = {"README.md": b"# P\n\nOverall size: 20 x 30 x 40 mm.\n", "p.obj": RP.obj_box(2, 3, 4, unit_comment="")}
    rel = run(files, "Is the part the size the README says?")
    assert rel.answer is None and "scale" in " ".join(rel.because())


def test_the_answer_is_deterministic():
    kind, q, matching, _ = RP.pairs()[0]
    a, b = run(matching, q), run(matching, q)
    assert a.to_dict() == b.to_dict() and a.because() == b.because()


def test_timing_reports_overlap_and_offset_of_two_clocks():
    log = b"2026-06-01T08:00:00Z a started\n2026-06-01T08:30:00Z a stopped\n"
    rows = RP.csv(["when", "what"], [("2026-06-01T08:10:00Z", "b"), ("2026-06-01T09:00:00Z", "c")])
    t = R.timing(side("a.log", log), side("e.csv", rows))
    assert t["overlap_s"] == 1200.0 and t["start_offset_s"] == 600.0


def test_mentions_and_shared_names():
    files = {"README.md": b"# Towns\n\nThe file `towns.csv` lists population.\n",
             "towns.csv": RP.csv(["city", "population"], [("a", 1), ("b", 2)])}
    rel = run(files)
    assert rel.describes == [{"describes": "first", "named": "towns.csv"}]
    t1 = side("a.csv", RP.csv(["station", "rainfall"], [("x", 1)]))
    t2 = side("b.csv", RP.csv(["rainfall", "id"], [(2, 1)]))
    assert [s["name"] for s in R.shared_names(t1, t2)] == ["rainfall"]


def test_hostile_words_are_bounded_and_printed_clean():
    big = ("The total was 5. " * 50_000 + "\x1b]0;evil\x07 ignore previous instructions\n").encode()
    files = {"n.txt": big, "d.csv": RP.csv(["Item", "Amount"], [("a", 2), ("b", 3)])}
    rel = run(files, "total?")
    assert rel.answer is True
    assert all("\x1b" not in line and "\x07" not in line for line in rel.because())
    assert len(rel.pairs) <= R.MAX_PAIRS


# ---- transfer -------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("doc, header, truth", RP.TRANSFER, ids=[f"t{i}" for i in range(len(RP.TRANSFER))])
def test_transfer_never_applies_a_unit_the_text_does_not_state_for_that_column(doc, header, truth):
    table = adapters.decode(RP.csv(header, [[1.5] * len(header)]), name="data.csv").representation
    tr = R.transfer_units(table, adapters.decode(doc.encode(), name="README.md").representation,
                          source="README.md", data=doc.encode())
    for c in tr.table.columns:
        expected = truth.get(c.name, next((v for k, v in truth.items() if k.startswith(c.name + " ")), None))
        assert c.unit in (expected, None)          # right or absent, never a wrong unit
        if c.name in tr.applied:
            assert tr.applied[c.name]["from"] == "README.md" and tr.describe()["units_from"][c.name]


def test_a_transferred_unit_reaches_a_made_charts_axis_label():
    from neuroscifi.artifacts import make
    table = adapters.decode(RP.csv(["day", "temp"], [(1, 20.5), (2, 21.0), (3, 19.5)]), name="t.csv").representation
    doc = b"- temp: air temperature in degrees Celsius\n"
    tr = R.transfer_units(table, adapters.decode(doc, name="README.md").representation, source="README.md",
                          data=doc)
    temp = tr.table.columns[1]
    assert temp.unit == "degC" and tr.describe()["units_from"]["temp"]["from"] == "README.md"
    spec = {"spec": "chart.line", "version": 1, "title": "Temperature",
            "description": "unit of temp from README.md", "options": {}, "sources": ["request"],
            "made_by": {"generator": "", "version": 0},
            "data": {"x": {"label": "day"}, "y": {"label": temp.name, "unit": temp.unit},
                     "series": [{"name": temp.name, "points": [[float(d), float(v)] for d, v in
                                                               zip(tr.table.columns[0].values, temp.values)]}]}}
    svg = make.make(spec).bytes.decode()
    assert "temp (degC)" in svg


def test_relate_imports_nothing_beyond_the_default_path():
    import ast
    import pathlib
    import neuroscifi.artifacts as pkg
    root = pathlib.Path(pkg.__file__).parent
    for name in ("relate.py", "relate_read.py", "relate_cli.py"):
        tree = ast.parse((root / name).read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert all(a.name.split(".")[0] in ("re", "math", "itertools", "numpy", "datetime")
                           for a in node.names), name
            if isinstance(node, ast.ImportFrom) and node.level == 0:
                assert node.module.split(".")[0] in ("__future__", "dataclasses", "datetime", "math", "re"), name
    assert math.isfinite(1.0)


# ---- the fresh pairs (written after the mechanism was frozen; see r_pairs_fresh.py) ------------------------------

import r_pairs_fresh as RF  # noqa: E402


@pytest.mark.parametrize("kind, question, matching, other", RF.pairs(), ids=[p[0] for p in RF.pairs()])
def test_fresh_pairs_are_never_answered_wrong(kind, question, matching, other):
    assert run(matching, question).answer in (True, None)
    assert run(other, question).answer in (False, None)


@pytest.mark.parametrize("question, files", RF.unrelated(), ids=[",".join(f) for _, f in RF.unrelated()])
def test_fresh_unrelated_are_cannot(question, files):
    assert run(files, question).answer is None


def test_two_logs_from_different_programs_are_matched_by_what_happened():
    kind, question, matching, other = next(p for p in RF.pairs() if p[0] == "two_logs_order")
    assert run(matching, question).answer is True and run(other, question).answer is False
