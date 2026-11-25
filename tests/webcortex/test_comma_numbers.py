"""A number written with its thousands separator is a quantity, never a year (the owner's ruling of 2026-11-25).

"2,900 residents" was formatted as "2900", which reads as the year 2900: two populations differed as "different
years", "2,900 residents" agreed with "in 2900", and "1,999 units" equalled the year 1999. Four bare digits keep the
year reading ("in 1999"). Knowledge and hypotheses stored when the old reading was current are read as their
statement reads now."""
import json

import pytest

from neuroscifi.instance import store
from neuroscifi.webcortex import consolidate, evolution
from neuroscifi.webcortex import text as T
from test_consolidate import T0, consolidated, open_db
from test_contradictions import fresh, known, sources  # noqa: F401  (fresh: the autouse fixture that clears caches)


@pytest.fixture
def db(tmp_path):
    yield from open_db(tmp_path)


@pytest.mark.parametrize("text, value", [
    ("2,900 residents live there.", "2,900"),
    ("1,999 units were sold.", "1,999"),
    ("It has 1,024 seats.", "1,024"),
    ("About 2.9 thousand residents live there.", "2,900"),      # a magnitude word writes no year either
    ("2900.0 residents live there.", "2,900"),
])
def test_a_grouped_or_scaled_number_is_a_quantity(text, value):
    assert T.values(text) == [value]
    assert T.value_kind(value) == ("number", "number:int", float(value.replace(",", "")))


@pytest.mark.parametrize("text", ["It ends in 2900.", "It was built in 1999.", "It sold 1999 units."])
def test_four_bare_digits_keep_the_year_reading(text):
    assert [T.value_kind(v)[0] for v in T.values(text)] == ["year"]


def test_two_populations_differ_as_quantities_not_as_years():
    a, b = "2,900 residents live there.", "The town has 3,100 residents."
    reason = T.values_conflict(T.values(a), T.values(b), nouns=(T.value_nouns(a), T.value_nouns(b)))
    assert reason == "different values: 2,900 vs 3,100"


def test_a_quantity_is_neither_equal_to_nor_the_same_as_a_year():
    assert T.values_equal("1,999", "1999") is None                  # another kind: not comparable, so not equal
    item = {"statement": "Orbix Station closes in 2900.", "claim_values": ["2900"], "polarity": 1}
    assert consolidate._compare(T.values("Orbix Station has 2,900 residents."), 1, item)[0] != "same"


def test_knowledge_stored_with_the_year_reading_is_compared_as_a_quantity(db):
    kid = known(db, "The town of Orbix has 2,900 residents.")
    with store.write(db):                       # as the parser before the ruling stored it
        db.execute("UPDATE web_knowledge SET claim_values=? WHERE id=?", (json.dumps(["2900"]), kid))
    item = {"statement": "The town of Orbix has 2,900 residents.", "claim_values": ["2900"], "polarity": 1}
    assert consolidate._read_values(item) == ["2,900"]
    assert consolidate._compare(["3,100"], 1, item) == ("conflict", "different values: 3,100 vs 2,900")
    _, out = consolidated(db, [sources("The town of Orbix has 3,100 residents.")], T0 + 10,
                          objective="How many residents?")
    assert [c["knowledge_id"] for c in out["contested"]] + [s["old"] for s in out["superseded"]] == [kid]


def test_a_hypothesis_stored_with_the_year_reading_is_settled_as_a_quantity():
    """evolution._relation reads a hypothesis's values from its statement, as a knowledge item's are read."""
    statement = "The town of Orbix has 2,900 residents."
    assert evolution._relation(["2,900"], 1, ["2900"], 1, statement) == "same"
    assert evolution._relation(["3,100"], 1, ["2900"], 1, statement) == "conflict"
    assert evolution._relation(["3,100"], 1, ["2900"], 1) != "same"          # without its statement: never the same


def test_a_stored_claim_is_read_as_its_text_reads_now():
    """web_claims.claim_values are parsed when a claim is stored; a task that resumes after the upgrade compares
    its stored claims as their text reads now (state.claim_from_row, state.claims)."""
    from neuroscifi.webcortex import state
    row = {name: None for name in state.CLAIM_COLUMNS} | {
        "id": "c1", "text": "The town of Orbix has 2,900 residents.", "source_url": "https://a.example/",
        "domain": "a.example", "title": "", "key": "orbix resid town", "kind": "sentence", "polarity": 1,
        "claim_values": ["2900"], "flags": {}}
    assert state.claim_from_row(row).values == ["2,900"]
    row["text"], row["claim_values"] = "Orbix Station closes in 2900.", ["2900"]
    assert state.claim_from_row(row).values == ["2900"]                       # a year stays a year


def test_a_monitor_baseline_saved_with_the_year_reading_reports_no_change():
    """A watch's baseline holds the values as they were stored. "2900" then and "2,900" now are one value; another
    number is still a change."""
    from neuroscifi.webcortex import scheduler

    def snapshot(values):
        return {"urls": {}, "claims": {"orbix resid town": {"values": values, "polarity": 1, "status": "corroborated",
                                                           "primary": False, "groups": 2, "relevance": 1.0}}}
    assert scheduler.compare(snapshot(["2900"]), snapshot(["2,900"]))["changed_values"] == []
    changed = scheduler.compare(snapshot(["2900"]), snapshot(["3,100"]))["changed_values"]
    assert [(c["before"], c["after"]) for c in changed] == [(["2900"], ["3,100"])]
