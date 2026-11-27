"""Fix lane F3, item 2 (ruling E): the relater's two misreadings found through the pipeline (lane int2).

1. A file's or a table's name is weak evidence of what its rows are: calls.csv may hold one row per day with a column
   of calls, so its row count is never by itself the number of calls.
2. "in" is inches only when it is adjacent to its number, not when it is the preposition before another word.
"""
from __future__ import annotations

import pytest

from neuroscifi.artifacts import adapters, relate as R, relate_read as rr
from neuroscifi.artifacts import types as T

CALLS = b"day,calls\nmon,100\ntue,110\nwed,95\nthu,105\nfri,110\n"        # 5 rows; the calls column sums to 520


def side(name, data):
    return R.side(adapters.decode(data, name=name, limits=T.Limits(refuse_flagged=False)), name=name, data=data)


def run(a, b, question=None):
    return R.compare(side(*a), side(*b), question)


def test_a_file_name_never_by_itself_turns_a_row_count_into_a_named_quantity():
    rel = run(("calls.csv", CALLS), ("notes.txt", b"The total number of calls was 520.\n"))
    assert rel.answer is not False, rel.reason               # it said DISAGREE: 5 rows against 520 "calls"
    for k in rel.decided_by:
        p = rel.pairs[k]
        assert not (p.a.ref.endswith(".rows") or p.b.ref.endswith(".rows")), p.how
    rel = run(("calls.csv", CALLS), ("notes.txt", b"The total number of calls was 520.\n"),
              "Is the number of calls in the summary the number in the file?")
    assert rel.answer is not False, rel.reason               # a column named calls: the name says what it holds


RESPONSES = b"id,score\n" + b"".join(b"%d,%d\n" % (i, i % 5) for i in range(1, 26))


def test_a_name_is_weak_alone_and_decides_when_the_question_counts_the_same_thing():
    summary = ("summary.txt", b"Summary.\nThe survey has 25 responses.\n")
    rel = run(("responses.csv", RESPONSES), summary)
    named = [p for p in rel.pairs if "weak evidence" in p.note]
    assert named and all(not p.strong for p in named) and rel.answer is None, rel.reason
    question = "Does the survey file hold the number of responses the summary gives?"
    assert run(("responses.csv", RESPONSES), summary, question).answer is True
    short = RESPONSES.rsplit(b"\n", 2)[0] + b"\n"                                  # 24 rows
    assert run(("responses.csv", short), summary, question).answer is False


def test_the_same_rows_named_by_the_statement_itself_still_decide():
    rel = run(("calls.csv", CALLS), ("notes.txt", b"The table has 5 rows.\n"))
    assert rel.answer is True, rel.reason
    rel = run(("calls.csv", CALLS), ("notes.txt", b"The table has 6 rows.\n"))
    assert rel.answer is False, rel.reason


@pytest.mark.parametrize("text, unit", [
    ("We took 520 in total.", None),
    ("520 in total", None),
    ("there were 5 in the box", None),
    ("5 in by March", None),
    ("It is 5 in.", "in"),
    ("5 in x 3 in", "in"),
    ("5 in by 3 in", "in"),
    ("a panel 5 in wide", "in"),
    ("5 in, 6 in", "in"),
    ("12 inches", "in"),
])
def test_in_is_inches_only_when_adjacent_to_its_number(text, unit):
    found = rr.numbers(text)
    assert found and (found[0].unit.symbol if found[0].unit else None) == unit


def test_a_total_in_words_is_not_compared_as_a_length():
    rel = run(("week.csv", CALLS), ("notes.txt", b"We took 520 calls, 520 in total.\n"))
    for p in rel.pairs:
        assert p.unit != "in", p.how
