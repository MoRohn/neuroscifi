"""What the self-critique judges: only sentences that do more than restate their cited claims (reasoning.restates).

Measured on the pipeline benchmark (docs/quality/evidence/accel_after_pipeline.json): the critique call was 42 % of a
research task, could remove nothing the guard had accepted, and 42 of its 46 marks sat on sentences that are a cited
claim word for word with the attribution the code appended. Such a sentence gives a second reading nothing to judge.
`restates` is exact: anything not provably a restatement is still judged. `critique_restatements` restores 0.7.0.
"""
import itertools
import json

import pytest

from fake_openai import make_gateway
from neuroscifi.inference import config as iconfig
from neuroscifi.instance import store
from neuroscifi.instance.errors import ValidationError
from neuroscifi.webcortex import reasoning
from test_inference_reasoning import Q, T0, answer, one_claim, report

CLAIM = "The build times dropped by 30 percent in our tests."
C1 = "The Zephyr compiler version 4.2.0 was released on 17 November 2026."      # REPORT: supported, sources 1 and 2
C2 = CLAIM                                                                      # REPORT: insufficient, source 3
C2_SHOWN = "The build times dropped by 30 percent in our tests, according to one source."
SHORT = "The build times dropped by 30 percent."
SHORT_SHOWN = "The build times dropped by 30 percent, according to one source."


def kept(b, *sentences):
    """What the answer path keeps of model sentences [(text, claim ids)]: compose(), then the guard."""
    value = {"sentences": [{"claims": list(c), "text": t} for t, c in sentences]}
    ok, bad = reasoning.guard(reasoning.compose(value, b), b)
    assert bad == [], bad
    return ok


def item(text, b, claims=("C1",), citations=None, **more):
    """An accepted item as the guard would hand it over, built by hand (for texts the guard would not accept)."""
    held = {c["id"]: c for c in b["claims"]}
    own = sorted({n for c in claims if c in held for n in held[c]["sources"]})
    return {"text": text, "claims": list(claims), "citations": own if citations is None else citations} | more


def two_claims(first="conflicting", second="insufficient"):
    return {"question": "How long is the Varnel River?", "verdict": None,
            "claims": [{"id": "C1", "state": first, "sources": [1], "published": None, "event": None,
                        "text": "The Varnel River is 412 kilometres long."},
                       {"id": "C2", "state": second, "sources": [2], "published": None, "event": None,
                        "text": "With a length of 412 km, the Varnel is the longest river of the Ostra region."}],
            "sources": [{"id": n, "title": f"Source {n}", "published": "undated"} for n in (1, 2)]}


# ---- restates: every attribution the code appends ----------------------------------------------------------------

@pytest.mark.parametrize("state", ["supported", *reasoning.HEDGES])
@pytest.mark.parametrize("sources", [(3,), (3, 4)])
def test_a_claim_word_for_word_with_the_composed_attribution_is_a_restatement(state, sources):
    """Every state compose() knows, one source and several: the kept sentence is the claim plus the code's hedge."""
    b = one_claim(state, sources)
    ok = kept(b, (CLAIM, ["C1"]))
    who = "one source" if len(sources) == 1 else "the cited sources"
    shown = CLAIM if state == "supported" else CLAIM[:-1] + ", " + reasoning.HEDGES[state].format(who=who) + "."
    assert ok[0]["text"] == shown and reasoning.restates(ok[0], b)


def test_a_state_the_table_does_not_know_is_attributed_and_recognised():
    b = one_claim("single_source")
    ok = reasoning.compose({"sentences": [{"claims": ["C1"], "text": CLAIM}]}, b)
    assert ok[0]["text"] == C2_SHOWN and reasoning.restates(item(ok[0]["text"], b), b)


@pytest.mark.parametrize("written", [
    CLAIM, CLAIM[:-1], "  The build   times dropped by 30 percent in our tests. ",
    "Yes, the build times dropped by 30 percent in our tests.",
    "According to one source, the build times dropped by 30 percent in our tests.",
    "The build times dropped by 30 percent in our tests, according to a report.",
])
def test_what_compose_normalises_away_does_not_make_a_sentence_the_models_own(written):
    """Spacing, the final stop, a leading answer word and the model's own attribution are replaced by code: the
    composed sentence is the same one, so it is a restatement."""
    b = one_claim("insufficient", (3,))
    ok = kept(b, (written, ["C1"]))
    assert reasoning._restate_key(ok[0]["text"]) == reasoning._restate_key(C2_SHOWN) and reasoning.restates(ok[0], b)


def test_the_attribution_the_guard_adds_itself_is_recognised():
    """R1-04: a sentence handed to the guard without attribution is attributed by the guard ("According to one
    source, ..."). Word for word it is a restatement; reworded (the R1-04 case itself) it is the model's own."""
    claim = "The first stable release, Rust 1.0, was published in May 2015."
    b = one_claim("insufficient", (1,), text=claim, question="When was the Rust programming language first released?")
    ok, bad = reasoning.guard([{"text": claim, "sources": [1], "claims": ["C1"]}], b)
    assert bad == [] and ok[0]["attributed"]
    assert ok[0]["text"] == "According to one source, the first stable release, Rust 1.0, was published in May 2015."
    assert reasoning.restates(ok[0], b)
    ok, bad = reasoning.guard([{"text": "The first stable release of Rust, version 1.0, was published in May 2015.",
                                "sources": [1], "claims": ["C1"]}], b)
    assert bad == [] and ok[0]["attributed"] and not reasoning.restates(ok[0], b)
    # the leading form is the guard's: without its mark the same text is compared with compose()'s form only
    assert not reasoning.restates(item("According to one source, the first stable release, Rust 1.0, was published "
                                       "in May 2015.", b), b)


# ---- restates: near misses are the model's own words ---------------------------------------------------------------

@pytest.mark.parametrize("text, why", [
    (SHORT_SHOWN, "shortened"),
    ("The build times dropped by 35 percent in our tests, according to one source.", "a changed number"),
    ("The build times dropped by 30 seconds in our tests, according to one source.", "a changed unit"),
    ("In our tests the build times dropped by 30 percent, according to one source.", "a reordered clause"),
    ("The build times dropped by 30 percent in our tests and are now the fastest, according to one source.",
     "an added clause"),
    ("The build times dropped sharply by 30 percent in our tests, according to one source.", "an added word"),
    ("The build time dropped by 30 percent in our tests, according to one source.", "a changed word form"),
    ("The build times dropped by 30 percent in our tests, according to the cited sources.", "another attribution"),
    ("The build times dropped by 30 percent in our tests, according to one source, though the sources disagree.",
     "a hedge of another state"),
    (CLAIM, "no attribution where the state requires one"),
    ("The build times dropped by 30 percent in our tests, according to one source, possibly.", "a trailing word"),
    ("The build times dropped by 30 percent in our tests?", "a question"),
    ("", "empty"),
])
def test_near_misses_are_not_restatements(text, why):
    b = one_claim("insufficient", (3,))
    assert not reasoning.restates(item(text, b), b), why


def test_a_dropped_negation_is_not_a_restatement():
    b = one_claim("supported", (3,), text="The library does not support Windows.")
    assert reasoning.restates(item("The library does not support Windows.", b), b)
    for text in ("The library does support Windows.", "The library supports Windows.",
                 "The library does not support Windows yet."):
        assert not reasoning.restates(item(text, b), b), text


def test_a_supported_claim_with_an_attribution_it_does_not_need_is_not_a_restatement():
    b = one_claim("supported", (3,))
    assert reasoning.restates(item(CLAIM, b), b)
    assert not reasoning.restates(item(C2_SHOWN, b), b)


def test_citations_or_claim_ids_that_are_not_the_cited_claims_own_are_judged():
    b = one_claim("insufficient", (3,))
    assert reasoning.restates(item(C2_SHOWN, b), b)
    assert not reasoning.restates(item(C2_SHOWN, b, citations=[3, 4]), b)       # a source the claim does not carry
    assert not reasoning.restates(item(C2_SHOWN, b, citations=[]), b)
    assert not reasoning.restates(item(C2_SHOWN, b, claims=("C1", "C9")), b)    # an unknown claim id
    assert not reasoning.restates(item(C2_SHOWN, b, claims=()), b)
    assert not reasoning.restates({"text": C2_SHOWN}, b)


# ---- restates: more than one cited claim is the model's composition ------------------------------------------------

def test_a_sentence_that_joins_cited_claims_is_the_models_composition_and_is_judged():
    """A restatement is one cited claim, word for word. Live (benchmark task F02): the model joined two cited claims
    with ", and" and the code appended the hedge of the weaker state; the guard accepts it, and the critic reads it.
    Whatever stands between the claims, in either order, with or without the capital of the second."""
    b = two_claims()
    a, c = (x["text"][:-1] for x in b["claims"])
    tail = ", according to the cited sources, though the sources disagree."
    live = kept(b, (f"{a}, and {c[0].lower()}{c[1:]}.", ["C1", "C2"]))
    assert live[0]["text"] == f"{a}, and {c[0].lower()}{c[1:]}{tail}" and not reasoning.restates(live[0], b)
    for first, second in itertools.permutations((a, c)):
        for glue in (", and ", " and ", "; ", "; and ", ", ", " because ", ", so ", " but ", " or "):
            for text in (first + glue + second + tail, first + glue + second[0].lower() + second[1:] + tail):
                assert not reasoning.restates(item(text, b, ("C1", "C2")), b), text
    # each of them alone is one
    assert reasoning.restates(item(a + ", according to one source, though the sources disagree.", b, ("C1",)), b)
    assert reasoning.restates(item(c + ", according to one source.", b, ("C2",)), b)


def test_two_claims_joined_can_say_what_neither_says_alone():
    """The security review's example, and the reason for the rule: one claim is a fragment, and joined to the other
    it borrows its predicate. "… ; the wheels are." says the wheels are affected, which no claim says."""
    b = two_claims("supported", "supported")
    b["claims"][0]["text"] = "NumPy 2.5.5 is not affected by the flaw if built from source"
    b["claims"][1]["text"] = "the wheels are"
    for text in ("NumPy 2.5.5 is not affected by the flaw if built from source; the wheels are.",
                 "NumPy 2.5.5 is not affected by the flaw if built from source, and the wheels are.",
                 "the wheels are; NumPy 2.5.5 is not affected by the flaw if built from source."):
        assert not reasoning.restates(item(text, b, ("C1", "C2")), b), text
        assert not reasoning.restates(item(text, b, ("C1",), citations=[1, 2]), b)      # nor under one claim's id
    assert reasoning.restates(item("NumPy 2.5.5 is not affected by the flaw if built from source.", b, ("C1",)), b)


def test_a_claim_cited_but_not_restated_is_judged():
    """Two claims cited, one restated: the attribution counts sources whose claim the sentence does not say."""
    b = two_claims()
    a, c = (x["text"][:-1] for x in b["claims"])
    tail = ", according to the cited sources, though the sources disagree."
    assert not reasoning.restates(item(a + tail, b, ("C1", "C2")), b)
    assert not reasoning.restates(item(c + ", according to the cited sources.", b, ("C1", "C2")), b)
    assert reasoning.restates(item(a + ", according to one source, though the sources disagree.", b, ("C1",)), b)


def test_three_claims_or_the_same_words_under_two_ids_are_judged():
    b = two_claims("supported", "supported")
    b["claims"].append({"id": "C3", "state": "supported", "sources": [2], "published": None, "event": None,
                        "text": "Its basin covers 9,800 square kilometres."})
    a, c, d = (x["text"][:-1] for x in b["claims"])
    assert not reasoning.restates(item(f"{a}; {d}; {c}.", b, ("C1", "C2", "C3")), b)
    assert not reasoning.restates(item(f"{c}, and {a}, and {d}.", b, ("C1", "C2", "C3")), b)
    b["claims"][1]["text"] = b["claims"][0]["text"]                # two sources carrying the same words
    assert not reasoning.restates(item(f"{a}.", b, ("C1", "C2")), b)      # two claims cited: the model's choice
    assert reasoning.restates(item(f"{a}.", b, ("C1",)), b) and reasoning.restates(item(f"{a}.", b, ("C1", "C1")), b)


# ---- review round: bounded cost, qualifiers after a source phrase, differences a reader sees -------------------------

def many_claims(n):
    return {"question": "How long is the Varnel River?", "verdict": None,
            "claims": [{"id": f"C{i}", "state": "supported", "sources": [i], "published": None, "event": None,
                        "text": "The Varnel River is 412 kilometres long." if i == 1 else
                                f"The Varnel River is 412 kilometres long and item {i} holds."}
                       for i in range(1, n + 1)],
            "sources": [{"id": i, "title": f"Source {i}", "published": "undated"} for i in range(1, n + 1)]}


@pytest.mark.parametrize("n", [4, 9, 12])
def test_a_sentence_citing_more_claims_than_the_schema_allows_is_judged_without_a_search(monkeypatch, n):
    """Review F1: the guard also reads citation markup in the text ("[C1-C9; 2; 3; ...]"), so an accepted item can
    carry up to MAX_CLAIMS claims although the schema allows three. The first version tried every ordering of them:
    9 claims took 68.7 s, 12 would take a day, outside the task's time budget and steerable by a page. Nothing is
    enumerated any more: a sentence that cites more than one claim is judged."""
    def no_search(*args, **kwargs):
        raise AssertionError("restates() must not enumerate orderings of the cited claims")
    monkeypatch.setattr(itertools, "permutations", no_search)
    b = many_claims(n)
    refs = "; ".join(str(i) for i in range(2, n + 1))
    ok, bad = reasoning.guard([{"text": f"The Varnel River is 412 kilometres long [C1-C{n}; {refs}].", "sources": [1],
                                "claims": ["C1"]}], b)
    assert bad == [] and len(ok[0]["claims"]) == n > reasoning.MAX_SENTENCE_CLAIMS
    assert reasoning.restates(ok[0], b) is False
    # also when the text really is all of them, joined
    joined = "; ".join(c["text"][:-1] for c in b["claims"]) + "."
    assert reasoning.restates(item(joined, b, tuple(c["id"] for c in b["claims"])), b) is False


QUALIFIED = "The bridge is open according to the official records except on Sundays."


def test_a_claim_that_holds_an_attribution_phrase_is_always_judged():
    """Review F3: the claim "The flaw is fixed according to the release notes except in versions 2 and 3" was taken
    for restated by "The flaw is fixed, according to one source.", because the comparison ran the claim through the
    same attribution replacement as the sentence. The claim is compared as extracted, and a claim with its own
    "according to <source>" is judged whatever the sentence says."""
    for claim in (QUALIFIED, "The flaw is fixed according to the release notes except in versions 2 and 3.",
                  "Drug Z is safe according to the official advisory for adults over 65 only.",
                  "The bridge is open according to the official records."):
        b = one_claim("insufficient", (3,), text=claim)
        short = claim.split(" according to ")[0]
        for text in (f"{short}, according to one source.", f"{claim[:-1]}, according to one source.", claim):
            assert not reasoning.restates(item(text, b), b), (claim, text)
    # through the answer path: what the model shortens itself passes the guard as any shortening does, and the
    # critic reads it
    b = one_claim("insufficient", (3,), text=QUALIFIED)
    for written in ("The bridge is open.", "The bridge is open according to the official records."):
        ok = kept(b, (written, ["C1"]))
        assert ok[0]["text"] == "The bridge is open, according to one source." and not reasoning.restates(ok[0], b)
    b = one_claim("supported", (3,), text=QUALIFIED)              # supported: nothing appended, nothing removed
    ok = kept(b, (QUALIFIED, ["C1"]))
    assert ok[0]["text"] == QUALIFIED and not reasoning.restates(ok[0], b)


ADVISORY = "The advisory says the library is safe to use in production."


@pytest.mark.parametrize("claim, written, words", [
    (QUALIFIED, QUALIFIED, "except on Sundays"),                                   # review F3: the claim's exception
    ("The flaw is fixed according to the release notes except in versions 2 and 3.",
     "The flaw is fixed according to the release notes except in versions 2 and 3.", "except in versions 2 and 3"),
    ("The flaw is fixed according to the release notes except in versions 2 and 3.",
     "The flaw is fixed according to the release notes in versions 2 and 3.", "in versions 2 and 3"),
    ("NumPy 2.5.5 is safe to install according to the release notes unless installed from PyPI.",
     "NumPy 2.5.5 is safe to install according to the release notes if installed from PyPI.",
     "if installed from PyPI"),
    (ADVISORY, "The library is safe to use, according to the cited sources from the advisory.",
     "from the advisory"),                                                         # review S4: no doubled attribution
    ("The bridge is open.", "The bridge is open according to a report by Reuters.", "by Reuters"),     # the cost
])
def test_a_sentence_whose_attribution_would_take_words_with_it_is_refused_not_shown_cut(claim, written, words):
    """The qualifier-dropping defect of 0.7.0 (review F3): compose() replaces a trailing "according to <source>"
    together with up to 40 characters after it, so "... according to the official records except on Sundays" was
    shown as "The bridge is open, according to one source.". compose() and the guard judge the sentence exactly as
    before; when it would be accepted, it is refused instead, with a plain reason (CUT_WORDS). The words can also be
    the model naming its source ("by Reuters"): code cannot tell, and that sentence is refused too."""
    b = one_claim("insufficient", (3,), text=claim)
    items = reasoning.compose({"sentences": [{"claims": ["C1"], "text": written}]}, b)
    assert items[0]["cut"] == [written, words] and written != items[0]["text"]
    assert items[0]["text"].endswith(", according to one source.")             # the text 0.7.0 composed, unchanged
    ok, bad = reasoning.guard(items, b)
    assert ok == [] and bad == [{"text": written, "reasons": [reasoning.CUT_WORDS.format(words=words)]}]
    assert "replacing the attribution would drop them" in bad[0]["reasons"][0]


def test_an_attribution_with_nothing_after_it_is_replaced_as_before():
    b = one_claim("insufficient", (3,), text="The bridge is open.")
    for written in ("The bridge is open, according to the benchmark.", "The bridge is open according to one source.",
                    "According to the cited sources, the bridge is open.", "The bridge is open."):
        items = reasoning.compose({"sentences": [{"claims": ["C1"], "text": written}]}, b)
        assert items == [{"text": "The bridge is open, according to one source.", "sources": [3], "claims": ["C1"]}]
        assert reasoning.guard(items, b)[1] == []
    b = one_claim("supported", (3,), text=QUALIFIED)              # no attribution is appended: nothing is cut
    items = reasoning.compose({"sentences": [{"claims": ["C1"], "text": QUALIFIED}]}, b)
    assert items == [{"text": QUALIFIED, "sources": [3], "claims": ["C1"]}]


def test_a_refused_sentence_leaves_every_other_verdict_as_it_was():
    """The refusal is added after the guard's own judgement: the refused sentence still counts as the earlier
    sentence it would have been, so "another source" citing the same source is rejected as before."""
    b = one_claim("insufficient", (3,), text="The bridge is open.")
    first = reasoning.compose({"sentences": [{"claims": ["C1"], "text": "The bridge is open according to a report "
                                                                        "by Reuters."}]}, b)[0]
    second = {"text": "Another source says the bridge is open.", "sources": [3], "claims": ["C1"]}
    plain = {k: v for k, v in first.items() if k != "cut"}
    before = reasoning.guard([plain, dict(second)], b)
    after = reasoning.guard([first, dict(second)], b)
    assert [x["text"] for x in before[0]] == ["The bridge is open, according to one source."] and after[0] == []
    assert before[1] == after[1][1:] and "another source" in before[1][0]["reasons"][0]
    assert after[1][0]["reasons"] == [reasoning.CUT_WORDS.format(words="by Reuters")]


def test_an_aside_attribution_is_composed_as_in_0_7_0():
    """Recorded, not wanted: an "according to the cited sources" the model sets between commas is not replaced (the
    replacement reads the start and the end of a sentence only), so one source can be shown as "..., according to
    the cited sources, ..., according to one source.". This is 0.7.0's behaviour and stays until it is changed
    together with the attack suite; such a sentence is no restatement, so the critic reads it."""
    b = one_claim("insufficient", (3,), text=ADVISORY)
    ok = kept(b, ("The library, according to the cited sources, is safe to use.", ["C1"]))
    assert ok[0]["text"] == "The library, according to the cited sources, is safe to use, according to one source."
    assert not reasoning.restates(ok[0], b)


def test_the_anchor_rule_holds_a_number_to_what_its_claim_says_of_it():
    """The guard's anchor rule as it is at 0.7.0, which the critique scope leaves alone: the negated version stated
    plainly, one version given what the claim says of another, and a list joined by another conjunction."""
    b = one_claim("supported", (3,), text="The parser flaw affects Zephyr 4.1 but not 4.2.",
                  question="Which versions are affected?")
    ok, bad = reasoning.guard([{"text": "The parser flaw affects Zephyr 4.2.", "sources": [3], "claims": ["C1"]}], b)
    assert ok == [] and any("4.2" in r for r in bad[0]["reasons"]), bad
    b = one_claim("supported", (3,), text="Zephyr 4.1 has the parser flaw and Zephyr 4.2 has the fix.",
                  question="Which versions are affected?")
    ok, bad = reasoning.guard([{"text": "Zephyr 4.2 has the parser flaw.", "sources": [3], "claims": ["C1"]}], b)
    assert ok == [] and any("4.2" in r for r in bad[0]["reasons"]), bad
    # another conjunction is another claim
    for claim, text in (("The parser flaw affects Zephyr 4.1 but not 4.2.", "The parser flaw affects Zephyr 4.1 and "
                                                                           "4.2."),
                        ("The parser flaw affects Zephyr 4.1 or 4.2.", "The parser flaw affects Zephyr 4.1 and 4.2."),
                        ("The parser flaw affects Zephyr 4.1 and 4.2.", "The parser flaw affects Zephyr 4.1 but not "
                                                                        "4.2."),
                        ("The parser flaw affects Zephyr 4.1 and 4.2.", "The parser flaw does not affect Zephyr 4.1 "
                                                                        "and 4.2.")):
        b = one_claim("supported", (3,), text=claim, question="Which versions are affected?")
        ok, bad = reasoning.guard([{"text": text, "sources": [3], "claims": ["C1"]}], b)
        assert ok == [] and bad, (claim, text)


@pytest.mark.parametrize("claim, text, why", [
    ("The sample contained CO at 5 ppm.", "The sample contained Co at 5 ppm, according to one source.",
     "carbon monoxide and cobalt differ by case"),
    ("The US approved it in May.", "the us approved it in may, according to one source.", "case"),
    ("The US approved it in May.", "The Us approved it in May, according to one source.", "case inside a word"),
    ("The package includes A, B, C…", "The package includes A, B, C, according to one source.",
     "a marked truncation dropped"),
    ("The package includes A, B, C...", "The package includes A, B, C, according to one source.",
     "a marked truncation dropped"),
    ("The package includes A, B, C…", "The package includes A, B, C..., according to one source.",
     "the guard reads the claim differently from how it is written"),
    ("The vote passed ‮7 to 2.", "The vote passed 7 to 2, according to one source.",
     "a right-to-left override in the claim shows 72 as 27"),
    ("The vote pas​sed 7 to 2.", "The vote passed 7 to 2, according to one source.", "a zero-width space"),
    ("The vote passed 7 to 2.", "The vote passed ‮7 to 2, according to one source.", "an override in the text"),
    ("The vote passed 7 to 2.", "The vote passed­ 7 to 2, according to one source.", "a soft hyphen"),
    ("The vote passed 7 to 2️.", "The vote passed 7 to 2, according to one source.", "a variation selector"),
    ("The vote passed 7 to 2ㅤ.", "The vote passed 7 to 2, according to one source.", "a filler"),
    ("The safe dose is 10⁶ units per day.", "The safe dose is 106 units per day, according to one source.",
     "a superscript"),
    ("The ofﬁce opened in 2019.", "The office opened in 2019, according to one source.", "a ligature"),
    ("The report calls it “safe”.", 'The report calls it "safe", according to one source.',
     "other quotation marks"),
    ("The range is 2–3 km.", "The range is 2-3 km, according to one source.", "another dash"),
    ("The bridge is open!", "The bridge is open, according to one source.", "a dropped exclamation mark"),
])
def test_differences_a_reader_sees_are_not_restatements(claim, text, why):
    """Review F7 / L4: the first version compared after NFKC, punctuation folding and lower-casing, without format
    characters. The comparison is character for character now, and a sentence or claim with a character a reader
    does not see is always judged."""
    b = one_claim("insufficient", (3,), text=claim)
    assert not reasoning.restates(item(text, b), b), why


def test_spacing_and_the_final_stop_are_the_only_differences_allowed():
    b = one_claim("insufficient", (3,), text="The bridge carries 42 000 vehicles a day.")
    assert reasoning.restates(item("The bridge carries 42 000 vehicles a day, according to one source.", b), b)
    assert reasoning.restates(item("The bridge  carries 42 000 vehicles a day, according to one source", b), b)
    b = one_claim("supported", (3,), text="The plant was built in the U.S.")
    assert reasoning.restates(item("The plant was built in the U.S.", b), b)
    assert reasoning.restates(item("The plant was built in the U.S", b), b)
    assert not reasoning.restates(item("The plant was built in the US.", b), b)
    assert reasoning._restate_key("  A  b. ") == "A b" and reasoning._restate_key("A b..") == "A b.."
    assert reasoning._restate_key("") is None and reasoning._restate_key("a​b") is None


def test_the_guards_own_lead_lowers_exactly_what_the_guard_lowers():
    claim = "Rust 1.0 was published in May 2015."                     # no word of _LOWER_LEAD: the guard keeps "Rust"
    b = one_claim("insufficient", (1,), text=claim, question="When was the Rust programming language first released?")
    ok, bad = reasoning.guard([{"text": claim, "sources": [1], "claims": ["C1"]}], b)
    assert bad == [] and ok[0]["text"] == "According to one source, Rust 1.0 was published in May 2015."
    assert reasoning.restates(ok[0], b)
    assert not reasoning.restates(dict(ok[0], text="According to one source, rust 1.0 was published in May 2015."), b)
    assert not reasoning.restates(dict(ok[0], text="according to one source, Rust 1.0 was published in May 2015."), b)


# ---- apply: what the critic is shown -------------------------------------------------------------------------------

def critic_sentences(request):
    return request["messages"][1]["content"].split("Answer sentences:\n")[1].split("\n")


def test_an_answer_of_restatements_makes_no_critique_call_and_says_why(gateway, fake):
    fake.replies += [answer((C1, [1, 2], ["C1"]), (C2, [3], ["C2"]), gaps=["independent benchmarks"])]
    out = reasoning.apply(None, report(), None, question=Q, now=T0, gateway=gateway)
    assert out["status"] == "model" and [s["text"] for s in out["answer"]] == [C1, C2_SHOWN]
    assert len(fake.requests) == 1                                        # the answer call only
    assert out["critique"] == {"status": "skipped", "reason": reasoning.RESTATEMENTS_ONLY.format(n=2),
                               "judged": 0, "restatements": 2}
    assert "nothing to judge" in out["critique"]["reason"] and "--critique-restatements" in out["critique"]["reason"]
    assert all("flag" not in s for s in out["answer"]) and out["rejected"] == []
    assert {"text": "independent benchmarks", "by": "model"} in out["gaps"]      # the answer call's gaps are kept
    assert [r["purpose"] for r in gateway.ledger.recent(5)] == ["research_answer"]


@pytest.mark.parametrize("own_first", [True, False])
def test_only_the_models_own_sentences_are_numbered_and_a_flag_lands_on_the_right_one(gateway, fake, own_first):
    """The critic numbers what it is shown; its "1" is the answer's second sentence when the first is a restatement.
    A number beyond what it was shown is ignored (0.7.0 numbering would have marked the wrong sentence)."""
    own, restated = (SHORT, [3], ["C2"]), (C1, [1, 2], ["C1"])
    sentences = (own, restated) if own_first else (restated, own)
    fake.replies += [answer(*sentences),
                     {"content": json.dumps({"issues": [{"sentence": 1, "problem": "overstated", "note": "lab"},
                                                        {"sentence": 2, "problem": "unsupported"}],
                                             "gaps": ["an independent benchmark"]})}]
    out = reasoning.apply(None, report(), None, question=Q, now=T0, gateway=gateway)
    assert len(fake.requests) == 2
    shown = critic_sentences(fake.requests[1])
    assert shown == [f"1. {SHORT_SHOWN} [sources [3]; claims ['C2']]"]
    assert C1 in fake.requests[1]["messages"][1]["content"]          # the whole evidence bundle, as before
    by_claim = {s["claims"][0]: s for s in out["answer"]}
    assert [s["claims"] for s in out["answer"]] == [list(s[2]) for s in sentences]
    assert by_claim["C2"]["flag"] == {"problem": "overstated", "note": "lab", "confirmed": False}
    assert "flag" not in by_claim["C1"]
    assert out["critique"] == {"status": "ok", "removed": 0, "flagged": 1, "judged": 1, "restatements": 1}
    assert {"text": "an independent benchmark", "by": "model"} in out["gaps"]


def test_a_flag_an_objective_check_confirms_still_removes_the_right_sentence(gateway, fake, monkeypatch):
    fake.replies += [answer((C1, [1, 2], ["C1"]), (SHORT, [3], ["C2"])),
                     {"content": json.dumps({"issues": [{"sentence": 1, "problem": "missing_hedge"}], "gaps": []})}]
    real = reasoning.check_sentence
    calls = []

    def strict(s, b):
        out = real(s, b)
        calls.append(s["text"])
        return out[:5] + (["number/unit mismatch with the cited evidence: test"],) if len(calls) > 2 else out
    monkeypatch.setattr(reasoning, "check_sentence", strict)
    out = reasoning.apply(None, report(), None, question=Q, now=T0, gateway=gateway)
    assert calls[2] == SHORT_SHOWN                                        # the confirmed check ran on the judged one
    assert [s["text"] for s in out["answer"]] == [C1]
    assert out["critique"] == {"status": "ok", "removed": 1, "flagged": 0, "judged": 1, "restatements": 1}
    assert out["rejected"][-1] == {"text": SHORT_SHOWN, "reasons": [
        "self-critique: missing_hedge", "confirmed: number/unit mismatch with the cited evidence: test"]}


def test_sentences_that_paraphrase_or_combine_claims_reach_the_critic(gateway, fake):
    """Where a second reading can matter: the model reworded a claim, or wrote one sentence from two claims. Both pass
    the lexical guard and both are judged; the word-for-word sentence beside them is not."""
    r = report()
    r["findings"][1]["statement"] = "The build times dropped by 30 percent in our tests of the Zephyr compiler."
    combined = "Zephyr 4.2.0 was released on 17 November 2026 and the build times dropped by 30 percent."
    fake.replies += [answer((C1, [1, 2], ["C1"]),
                            ("Zephyr 4.2.0 was released on 17 November 2026.", [1, 2], ["C1"]),
                            (combined, [1, 2, 3], ["C1", "C2"])),
                     {"content": json.dumps({"issues": [{"sentence": 2, "problem": "overstated"}], "gaps": []})}]
    out = reasoning.apply(None, r, None, question=Q, now=T0, gateway=gateway)
    shown = critic_sentences(fake.requests[1])
    assert len(out["answer"]) == 3 and len(shown) == 2, (out["rejected"], shown)
    assert shown[0].startswith("1. Zephyr 4.2.0 was released on 17 November 2026. ")
    assert shown[1].startswith("2. Zephyr 4.2.0 was released on 17 November 2026 and the build times dropped by 30 "
                               "percent, according to the cited sources. ")
    assert [bool(s.get("flag")) for s in out["answer"]] == [False, False, True]
    assert out["critique"] == {"status": "ok", "removed": 0, "flagged": 1, "judged": 2, "restatements": 1}


def test_when_no_sentence_is_left_out_the_record_is_the_0_7_0_one(gateway, fake):
    """`judged` and `restatements` appear only when a restatement was left out: an answer of the model's own
    sentences is recorded exactly as 0.7.0 recorded it, which the 0.7.0 tests of this record still check."""
    fake.replies += [answer((SHORT, [3], ["C2"])),
                     {"content": json.dumps({"issues": [{"sentence": 1, "problem": "overstated"}], "gaps": []})}]
    out = reasoning.apply(None, report(), None, question=Q, now=T0, gateway=gateway)
    assert out["critique"] == {"status": "ok", "removed": 0, "flagged": 1} and len(fake.requests) == 2


def test_a_failed_critique_call_still_says_what_it_was_given(gateway, fake):
    fake.replies += [answer((C1, [1, 2], ["C1"]), (SHORT, [3], ["C2"])),
                     {"status": 400, "body": {"error": {"message": "bad request"}}}]
    out = reasoning.apply(None, report(), None, question=Q, now=T0, gateway=gateway)
    assert out["status"] == "model" and len(out["answer"]) == 2
    assert out["critique"]["status"] == "failed" and out["critique"]["judged"] == 1 \
        and out["critique"]["restatements"] == 1


# ---- the switch and the other skips --------------------------------------------------------------------------------

def test_with_critique_restatements_every_sentence_is_judged_as_in_0_7_0(fake, key_file, tmp_path):
    """The rollback: the request, the numbering and the stored `critique` are what 0.7.0 produced."""
    gw = make_gateway(fake, key_file, tmp_path, critique_restatements=True)
    fake.replies += [answer((C1, [1, 2], ["C1"]), (C2, [3], ["C2"])),
                     {"content": json.dumps({"issues": [{"sentence": 2, "problem": "overstated", "note": "lab"}],
                                             "gaps": []})}]
    out = reasoning.apply(None, report(), None, question=Q, now=T0, gateway=gw)
    assert len(fake.requests) == 2
    assert critic_sentences(fake.requests[1]) == [f"1. {C1} [sources [1, 2]; claims ['C1']]",
                                                  f"2. {C2_SHOWN} [sources [3]; claims ['C2']]"]
    assert out["critique"] == {"status": "ok", "removed": 0, "flagged": 1}
    assert out["answer"][1]["flag"] == {"problem": "overstated", "note": "lab", "confirmed": False}
    assert "flag" not in out["answer"][0]


def test_self_critique_off_makes_no_critique_call_and_records_none(fake, key_file, tmp_path):
    gw = make_gateway(fake, key_file, tmp_path, self_critique=False)
    fake.replies += [answer((SHORT, [3], ["C2"]))]
    out = reasoning.apply(None, report(), None, question=Q, now=T0, gateway=gw)
    assert out["status"] == "model" and out["critique"] is None and len(fake.requests) == 1


def test_the_voice_and_time_budget_skips_keep_their_reasons(fake, key_file, tmp_path, monkeypatch):
    """The earlier skips are decided first and worded as before, whatever the sentences are (here: restatements)."""
    gw = make_gateway(fake, key_file, tmp_path, critique_voice=False)
    fake.replies += [answer((C1, [1, 2], ["C1"]))]
    out = reasoning.apply(None, report(), None, question=Q, now=T0, gateway=gw, spoken=True)
    assert out["critique"]["status"] == "skipped" and out["critique"]["reason"].startswith("spoken answer")
    assert set(out["critique"]) == {"status", "reason"}
    (tmp_path / "b").mkdir()
    gw = make_gateway(fake, key_file, tmp_path / "b")
    monkeypatch.setattr(reasoning, "MIN_MODEL_SECONDS", 0.5)
    fake.replies += [{"delay": 0.7, **answer((C1, [1, 2], ["C1"]))}]
    out = reasoning.apply(None, report(), None, question=Q, now=T0, gateway=gw, time_left=1.0)
    assert out["status"] == "model" and len(fake.requests) == 2
    assert out["critique"] == {"status": "skipped",
                               "reason": "time_budget: too little of the task's time budget left"}


# ---- the setting ---------------------------------------------------------------------------------------------------

def test_critique_restatements_is_off_by_default_and_a_setting_saved_before_it_loads_with_the_default(tmp_path):
    assert iconfig.InferenceConfig().critique_restatements is False
    old = json.dumps({"base_url": "http://127.0.0.1:8811/v1", "self_critique": True, "critique_voice": False})
    cfg = iconfig.InferenceConfig.from_json(old)
    assert cfg.critique_restatements is False and cfg.critique_voice is False and cfg.self_critique is True
    db = store.create(tmp_path / "i.sqlite", T0)
    try:
        assert iconfig.get_config(db).critique_restatements is False
        assert iconfig.set_config(db, {"critique_restatements": True}, T0).critique_restatements is True
        assert iconfig.get_config(db).critique_restatements is True
        assert iconfig.public(iconfig.get_config(db))["critique_restatements"] is True
        with pytest.raises(ValidationError):
            iconfig.set_config(db, {"critique_restatements": "yes"}, T0)
    finally:
        db.close()


def test_the_cli_switch_sets_it_and_status_shows_it(tmp_path, capsys):
    from neuroscifi.cli import main
    path = str(tmp_path / "i.sqlite")
    store.create(path, T0).close()

    def run(*argv):
        code = main(["llm", *argv, "--db", path])
        return code, json.loads(capsys.readouterr().out)
    code, out = run("config")
    assert code == 0 and out["critique_restatements"] is False
    code, out = run("config", "--critique-restatements")
    assert code == 0 and out["critique_restatements"] is True
    code, out = run("status")
    assert code == 0 and out["reasoning"]["critique_restatements"] is True and out["reasoning"]["self_critique"] is True
    code, out = run("config", "--no-critique-restatements")
    assert code == 0 and out["critique_restatements"] is False
