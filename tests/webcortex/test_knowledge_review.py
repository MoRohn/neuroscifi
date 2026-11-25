"""Review of the remaining-items round (knowledge and units): stored values read by an older parser, values of kinds
that share no comparison, a statement naming one more numbered name, aliases after "previously", ties between
similar keys, conversions across units, more spellings of speeds, masking with a currency symbol."""
import json

import pytest

from neuroscifi.instance import store
from neuroscifi.webcortex import consolidate
from neuroscifi.webcortex import text as T
from test_consolidate import T0, consolidated, open_db
from test_contradictions import fresh, known, sources  # noqa: F401  (fresh: the autouse fixture that clears caches)


@pytest.fixture
def db(tmp_path):
    yield from open_db(tmp_path)


def _stored_as(db, kid, values):
    """The item as an older version of the value parser stored it (claim_values are parsed once, at insertion)."""
    with store.write(db):
        db.execute("UPDATE web_knowledge SET claim_values=? WHERE id=?", (json.dumps(values), kid))


def _touched(out):
    return [c["knowledge_id"] for c in out["contested"]] + [s["old"] for s in out["superseded"]]


@pytest.mark.parametrize("stored_text, old_values, new_text", [
    ("The speed limit on the Orbix motorway is 120 km/h.", ["120 km"],
     "The speed limit on the Orbix motorway is 130 km/h."),
    ("The Orbix appliance ships with 8 GiB of memory.", ["8"], "The Orbix appliance ships with 9 GiB of memory."),
    ("The Orbix kettle boils water at 212 ℉.", ["212"], "The Orbix kettle boils water at 200 ℉."),
])
def test_knowledge_stored_by_an_older_parser_is_read_as_its_statement_says(db, stored_text, old_values, new_text):
    """claim_values were parsed when the item was stored ("120 km/h" as "120 km" before speeds existed). A new
    statement of another value must still contradict it, never strengthen it."""
    kid = known(db, stored_text)
    _stored_as(db, kid, old_values)
    _, out = consolidated(db, [sources(new_text)], T0 + 10, objective="What does it say?")
    assert _touched(out) == [kid]


@pytest.mark.parametrize("new, statement, old_values", [
    (["8 gb"], "The Orbix appliance ships with 8 GiB of memory.", ["8"]),      # a bare 8 took the new side's GB
    (["130 km/h"], "The speed limit on the Orbix motorway is 120 km/h.", ["120 km"]),
])
def test_an_older_reading_of_the_stored_statement_is_not_the_same_as_a_new_value(new, statement, old_values):
    item = {"statement": statement, "claim_values": old_values, "polarity": 1}
    assert consolidate._compare(new, 1, item)[0] != "same"


def test_values_of_kinds_that_share_no_comparison_never_strengthen():
    """Both sides state values and none can be compared with the other's: the words alone do not make them one fact."""
    item = {"statement": "The Orbix kiosk draws 700 W.", "claim_values": ["700 w"], "polarity": 1}
    assert consolidate._compare(["3 kg"], 1, item)[0] != "same"
    assert consolidate._compare([], 1, item)[0] == "same"                  # one side without values: as before
    assert consolidate._compare(["700 w"], 1, item)[0] == "same"


def test_a_statement_naming_one_more_numbered_name_is_still_about_the_first(db):
    kid = known(db, "Drellis-9 orbits at an altitude of 780 kilometres.")
    _, out = consolidated(db, [sources("Drellis-9 orbits at an altitude of 640 kilometres, as does Drellis-8.")],
                          T0 + 10, objective="At what altitude does Drellis-9 orbit?")
    assert _touched(out) == [kid]


@pytest.mark.parametrize("statement, names", [
    ("The Orbix node needs 4GB of memory and draws 700W.", set()),
    ("Each tablet holds 50mg of the drug.", set()),
    ("Drellis-9 and H100 and GPT-4 are named.", {"drellis-9", "h100", "gpt-4"}),
])
def test_a_value_written_against_its_unit_is_not_a_numbered_name(statement, names):
    assert consolidate._numbered_names(statement) == names


@pytest.mark.parametrize("statement", [
    "Ann Smith, previously CEO of Orbix, earns 5 million dollars.",
    "Orbix acquired Globex, previously Initech's parent company, in 2019.",
    "The Brask Line, previously Northern Rail's spur, is 44 km long.",
    "Ann Smith, formerly President, earns 5 million dollars.",
    "The Old Lady, also known as the Bank of England, holds 310 tonnes of gold.",
])
def test_a_role_or_a_description_after_previously_is_not_an_alias(statement):
    assert consolidate._aliases([{"id": 1, "statement": statement}]) == []


def test_ties_between_similar_keys_go_to_the_first_item(monkeypatch):
    """Two live items with keys equally similar and of one version: the one stored first, as before."""
    rows = [(5, "a b c d e", 1), (9, "a b c d f", 1)]
    picked = []
    monkeypatch.setattr(consolidate, "_similar", lambda a, b: 0.9)
    monkeypatch.setattr(consolidate.knowledge, "rows", lambda _db, *a, **k: [])
    monkeypatch.setattr(consolidate.knowledge, "row",
                        lambda _db, kid: picked.append(kid) or {"id": kid, "statement": "x", "version": 1})

    class Db:
        def execute(self, sql, params=()):
            return iter(rows)

    class Cluster:
        claims, statement, key = [], "x", "a b c d"
    assert consolidate._existing(Db(), Cluster())["id"] == 5 and picked == [5]


@pytest.mark.parametrize("a, b", [
    ("60.5 mph", "97.4 km/h"),          # 97.37 km/h written to one decimal
    ("26.2 mi", "42.2 km"),             # a marathon
    ("1.5 mi", "2.4 km"),
])
def test_values_converted_from_other_units_agree_within_their_written_precision(a, b):
    assert T.values_conflict([a], [b]) is None


@pytest.mark.parametrize("a, b", [
    ("6.12 km", "6.14 km"),             # one unit, the same decimals: not a rounding
    ("100.5 mph", "162.5 km/h"),        # 0.5 % apart: more than the rounding of either
])
def test_values_written_to_the_same_decimals_still_differ_when_rounding_cannot_explain_it(a, b):
    assert T.values_conflict([a], [b]) is not None


@pytest.mark.parametrize("text, value", [
    ("It reaches 100 kilometres an hour.", "100 km/h"),
    ("It reaches 100 km per hour.", "100 km/h"),
    ("It reaches 60 mi/h.", "60 mph"),
    ("It reaches 60 miles an hour.", "60 mph"),
    ("The probe travels at 7.8 km/s.", "7.8 km/s"),
])
def test_more_spellings_of_speeds(text, value):
    assert T.values(text) == [value]


@pytest.mark.parametrize("text, wrong", [
    ("The crane lifts 50 kN.", "50 kn"),            # kilonewtons, not knots
    ("The disk writes 5 MB/s.", "5 mb"),           # a rate, not a size
    ("Gravity is 9.8 m/s² here.", "9.8 m"),
])
def test_a_rate_or_another_unit_is_not_read_as_a_size_a_length_or_a_speed(text, wrong):
    assert wrong not in T.values(text)


def test_masking_keeps_the_currency_when_the_other_statement_writes_a_symbol():
    assert "euros" in T.mask_quantities("610 million euros", like="€0.61 bn")


# ---- fix round 2 (N1): a rate keeps its compound unit, so it compares only with the same rate ---------------------

@pytest.mark.parametrize("text, value", [
    ("The disk writes 5 MB/s.", "5 mb/s"),
    ("Gravity is 9.8 m/s² here.", "9.8 m/s²"),
    ("The link carries 10 Gb/s.", "10 gb/s"),
    ("The drive reads 500 MiB/s.", "500 mib/s"),
])
def test_a_rate_is_read_with_its_compound_unit(text, value):
    assert T.values(text) == [value]


@pytest.mark.parametrize("a, b", [
    ("The drive copies 500 MB/s.", "The drive copies 7000 MB/s."),
    ("Gravity on the surface is 9.8 m/s².", "Gravity on the surface is 3.7 m/s²."),
    ("The link carries 10 Gb/s.", "The link carries 1 Gb/s."),
])
def test_two_different_rates_conflict(a, b):
    assert T.values_conflict(T.values(a), T.values(b)) is not None


def test_equal_rates_agree_and_a_rate_never_compares_with_a_size():
    assert T.values_agree(T.values("It copies 500 MB/s."), T.values("It copies 500 megabytes/s."))
    assert T.values_equal("5 mb/s", "5 mb") is None
    assert T.values_equal("5 mb/s", "9 mb/s") is False


# ---- fix round 2 (N2): a shared year or date does not make the other values comparable ---------------------------

@pytest.mark.parametrize("new, statement, stored", [
    (["2024", "130 km/h"], "In 2024 the Orbix motorway limit was 120 km.", ["2024", "120 km"]),
    (["2024", "8 gb"], "In 2024 the Orbix appliance shipped 16 GiB.", ["2024", "16 gib"]),
    (["2024-03-01", "5 mb/s"], "On 1 March 2024 the Orbix disk held 5 MB.", ["2024-03-01", "5 mb"]),
])
def test_a_shared_date_does_not_make_incomparable_values_the_same(new, statement, stored):
    item = {"statement": statement, "claim_values": stored, "polarity": 1}
    assert consolidate._compare(new, 1, item)[0] == "incomparable"


def test_a_shared_date_with_comparable_values_still_compares():
    item = {"statement": "In 2024 the Orbix motorway was 120 km long.", "claim_values": ["2024", "120 km"],
            "polarity": 1}
    assert consolidate._compare(["2024", "120 km"], 1, item)[0] == "same"
    assert consolidate._compare(["2024", "130 km"], 1, item)[0] == "conflict"
    assert consolidate._compare(["2024"], 1, item)[0] == "same"          # one side without other values: as before


def test_a_stored_size_is_not_strengthened_by_a_rate(db):
    kid = known(db, "The Orbix cartridge holds 5 MB.")
    _, out = consolidated(db, [sources("The Orbix cartridge holds 9 MB/s.")], T0 + 10, objective="What does it hold?")
    assert kid not in out.get("strengthened", [])
