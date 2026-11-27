"""Wave O-1 integration, items 2 and 3: a production with several sources relates each pair by measurement (lane R's
`relate.compare`, bounded) and says what was found in the made document's own section, with both values where they
disagree; the links are recorded as `nsf artifact relate` records them (lane FX5, C4: a relation found with no
question is shown and records no consistent_with / contradicts link; `Relation.specific` is the one gate). A unit a README states for a table's column
reaches the made chart's axis label and the table's header with where it was read. Every input is built here, so the
true relation is known: the CSV's calls sum to 500."""
import pytest

from neuroscifi.artifacts import check
from neuroscifi.artifacts import produce as P
from neuroscifi.artifacts import store as A
from neuroscifi.instance import store

from test_produce import T0, wav_tones

WEEK = b"day,calls\nmon,120\ntue,95\nwed,130\nthu,80\nfri,75\n"          # 500 calls in all


def report(total: int) -> bytes:
    return f"# Weekly calls\n\nThe total of calls was {total}.\n".encode()


@pytest.fixture
def individual(tmp_path):
    db = store.create(tmp_path / "individual.sqlite", T0)
    try:
        yield db
    finally:
        db.close()


def section(spec) -> list:
    blocks = spec.data["blocks"]
    start = next(i for i, b in enumerate(blocks) if b["kind"] == "heading" and b["text"] == "How the sources relate")
    return blocks[start:]


def links(db, relation) -> list:
    return db.execute("SELECT src, dst_id, how FROM artifact_links WHERE relation=?", (relation,)).fetchall()


def test_a_report_whose_stated_total_disagrees_with_its_csv_is_shown_and_nothing_is_stored(individual):
    # Team lead's rulings of 2026-11-27 (lane FX5): C4 option (A), with no question a production's relations
    # between its sources are shown and never stored as consistent_with / contradicts; C6(b), with no question a word
    # inside a sentence ("total") never selects an aggregate, so the stated total is not compared with the column's
    # sum. Before lane FX5 this case said "They DISAGREE: 500 against 520" and stored a contradicts link.
    db = individual
    result = P.produce(db, "a report of the week", [P.Given("week.csv", WEEK), P.Given("report.md", report(520))],
                       form="document", now=T0)
    assert result.status == "made" and result.spec.spec == "document.html"
    blocks = section(result.spec)
    assert blocks[2] == {"kind": "heading", "level": 2, "text": "week.csv and report.md"}
    assert blocks[3]["text"].startswith("Cannot tell: with no question, a stated number is compared only")
    html = A.read_bytes(db, result.artifact).decode()
    assert "How the sources relate" in html
    assert check.check(A.read_bytes(db, result.artifact), "text/html", A.spec_of(db, result.artifact)).ok
    # shown, not resolved: each source keeps its own section, and the CSV's values are shown as they are
    own = result.spec.data["blocks"][:len(result.spec.data["blocks"]) - len(blocks)]
    assert [b["text"] for b in own if b["kind"] == "heading" and b["level"] == 1] == ["week.csv", "report.md"]
    bars = next(b["figure"]["data"] for b in own if b["kind"] == "figure")
    assert bars["series"][0]["values"] == [120, 95, 130, 80, 75]
    assert result.record["relations"]["pairs"][0]["answer"] is None
    assert links(db, "contradicts") == [] and links(db, "consistent_with") == []


def test_a_relation_tied_by_its_own_words_is_shown_with_both_numbers_and_nothing_is_stored(individual):
    # the same rulings: a duration its sentence names is compared with no question (C6b), shown with both values,
    # and never stored (C4 (A)); an owner records such a link by asking a question the grammar reads completely
    from test_produce import wav_tones
    db = individual
    for note, verdict in ((b"Field notes.\nThe recording lasts 3 seconds.\n", "They DISAGREE"),
                          (b"Field notes.\nThe recording lasts 1 seconds.\n", "They agree")):
        result = P.produce(db, "a document of both", [P.Given("notes.txt", note), P.Given("clip.wav", wav_tones())],
                           form="document", now=T0)
        blocks = section(result.spec)
        assert blocks[3]["text"].startswith(verdict)
        found = [b["text"] for b in blocks if b["kind"] == "list_item"]
        assert len(found) == 1 and (" against " in found[0])
    assert links(db, "contradicts") == [] and links(db, "consistent_with") == []


def test_sources_that_share_nothing_measurable_are_said_to_be_not_comparable(individual):
    result = P.produce(None, "a document of both", [P.Given("tones.wav", wav_tones()),
                                                     P.Given("week.csv", WEEK)], form="document")
    blocks = section(result.spec)
    assert blocks[3]["text"].startswith("Cannot tell")
    found = [b["text"] for b in blocks if b["kind"] == "list_item"]
    # what was tried and could not be compared is said (a tone's pitch against a column's values), nothing else
    assert found and all(t.startswith("Cannot be compared: ") for t in found)
    assert result.record["relations"]["pairs"][0]["answer"] is None
    assert result.record["relations"]["pairs"][0]["not_compared"] == len(found)


def test_pairs_are_bounded_and_what_was_skipped_is_said(monkeypatch):
    monkeypatch.setattr(P, "MAX_RELATE_PAIRS", 2)
    result = P.produce(None, "a document of all three", [P.Given("week.csv", WEEK), P.Given("report.md", report(520)),
                                                          P.Given("tones.wav", wav_tones())], form="document")
    rel = result.record["relations"]
    assert (rel["compared"], rel["of"], rel["skipped"]) == (2, 3, 1)
    assert section(result.spec)[-1]["text"] == "1 of 3 pairs were not compared: at most 2 pairs of sources are compared."


def test_an_answer_in_words_still_says_what_was_measured(individual):
    result = P.produce(individual, "what is the total?", [P.Given("week.csv", WEEK), P.Given("report.md", report(520))],
                       now=T0)
    if result.status == "words":
        assert any(w.startswith("week.csv and report.md: They DISAGREE") for w in result.words)
        assert any("500 against 520" in w for w in result.words)
    else:
        assert "DISAGREE: 500 against 520" in str(result.spec.data)


def test_one_source_is_related_to_nothing():
    result = P.produce(None, "a document", [P.Given("week.csv", WEEK)], form="document")
    assert "relations" not in result.record


TEMPS = ("date,temp,rain\n" + "".join(f"2025-01-{d:02d},{d % 7 + 0.5},{d % 3}\n" for d in range(1, 29))).encode()
README = b"# Data\n\nThe column temp is in degrees Celsius.\n"


def test_a_unit_a_readme_states_reaches_the_axis_label_and_the_table_header_with_its_source():
    chart = P.produce(None, "how did temp change", [P.Given("t.csv", TEMPS), P.Given("README.md", README)])
    figure = next(b["figure"] for b in chart.spec.data["blocks"] if b["kind"] == "figure")
    assert figure["spec"] == "chart.line" and figure["data"]["y"] == {"label": "temp", "unit": "degC, from README.md"}
    assert "temp (degC, from README.md)" in chart.made.bytes.decode()
    assert chart.record["relations"]["units"] == [{"table": "t.csv", "column": "temp", "unit": "degC",
                                                   "from": "README.md"}]
    table = P.produce(None, "show it", [P.Given("t.csv", TEMPS), P.Given("README.md", README)], form="table")
    cols = next(b["table"]["columns"] for b in table.spec.data["blocks"] if b["kind"] == "table")
    units = {c["name"]: c.get("unit") for c in cols}
    assert units == {"date": None, "temp": "degC, from README.md", "rain": None}    # rain: no unit is stated
    assert "temp <span class=\"unit\">(degC, from README.md)</span>" in table.made.bytes.decode()


def test_no_unit_travels_without_a_statement_for_that_column_or_from_one_source_alone():
    other = b"# Data\n\nRain is measured daily.\n"
    made = P.produce(None, "show it", [P.Given("t.csv", TEMPS), P.Given("README.md", other)], form="table")
    assert "units" not in made.record["relations"]
    alone = P.produce(None, "how did temp change", [P.Given("t.csv", TEMPS)])
    assert "unit" not in alone.spec.data["y"]
