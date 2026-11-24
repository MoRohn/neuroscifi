"""The refusals and the one exemption added to the guard after 0.7.0, each by its own rule.

The verdicts of whole sentences are held by the gap corpora (tests/inference/fixtures/guard_corpus_gaps.json through
tests/inference/test_guard_differential.py); these tests pin the parts the rules are made of.
"""
import pytest

from neuroscifi.webcortex import reasoning
from test_inference_reasoning import one_claim


def judged(b, text, ids=("C1",)):
    """(accepted, rejected) of one model sentence through compose() and the guard."""
    return reasoning.guard(reasoning.compose({"sentences": [{"claims": list(ids), "text": text}]}, b), b)


# ---- a claim's restriction ---------------------------------------------------------------------------------------

@pytest.mark.parametrize("text, marker", [
    ("Every release is patched except 2.5.4 and 2.5.5", "except"),
    ("Except for 2.5.4, every release is patched", "Except"),
    ("Every endpoint requires a token with the exception of the health check", "with the exception of"),
    ("The update covers every model excluding the 2019 series", "excluding"),
    ("The fee covers all rides, not including the ferry", "not including"),
    ("The flaw is fixed in all versions other than 2.5.4", "other than"),
    ("All regions are back online apart from Frankfurt", "apart from"),
    ("All regions are back online aside from Frankfurt", "aside from"),
    ("The service was restored everywhere save for the Tolvane district", "save for"),
    ("Barring the 2019 series, every model is covered", "Barring"),
    ("All supported versions are affected but not the 2.5.5 release", "but not"),
    ("All but the oldest release are patched", "All but"),
    ("Everything but the parser was rewritten", "Everything but"),
    ("The wheels are safe to install unless the build flag is set", "unless"),
    ("The exploit works only if the debug port is open", "only if"),
    ("The valve opens only when the tank is full", "only when"),
    ("The warranty covers water damage provided that the seal is intact", "provided that"),
    ("The cache stays consistent as long as one writer is active", "as long as"),
    ("The cache stays consistent so long as one writer is active", "so long as"),
    ("The grant is paid on condition that the audit passes", "on condition that"),
    ("The library is safe when used with TLS 1.3", "when used"),
    ("The engine is quiet when properly installed", "when properly installed"),
    ("NumPy 2.5.5 is not affected by the flaw when built from source", "when built"),
    ("NumPy 2.5.5 is not affected by the flaw if built from source", "if"),
    ("If the seal is broken, the warranty is void", "If"),
    ("The flaw is confirmed fixed if the patch is applied", "if"),
    ("The   flaw is fixed  other\tthan in 2.5.4", "other than"),
])
def test_a_restriction_is_found_by_its_closed_list_of_words(text, marker):
    assert reasoning._restriction(text) == marker


@pytest.mark.parametrize("text", [
    "The release shipped on 4 June 2026",
    "The team said it would ship even if two tests fail",                        # concedes
    "The server responded as if the cache were empty",                           # compares
    "It is unclear if the patch applies to version 3",                           # asks
    "The vendor did not say if the patch covers version 3",
    "Nobody knows whether or if the port was open",
    "The audit found few defects, if any",                                       # idioms
    "Most, if not all, regions are back online",
    "The flaw is rarely, if ever, exploited",
    "When the ship sank in 1628 it was less than a mile from port",              # a when of time, with its subject
    "When asked about the outage, the operator said service returned at noon",   # a when that reports
    "The bridge is closed until May 2027",                                       # bounds of time: left out on purpose
    "The flaw was reported before the release and fixed after it",
    "The exception handler logs every unexpected error",                         # the words inside other words
    "The gift certificate is valid in all stores",
    "The whenever-ready build passed",
])
def test_words_that_restrict_nothing_are_not_restrictions(text):
    assert reasoning._restriction(text) is None


def test_a_restricted_claim_is_restated_whole_or_refused():
    """0.7.0 accepted every one of the refused sentences: a sentence without the claim's exception holds only words of
    the claim, which is all the entailment check counts."""
    claim = "Every NumPy release is patched against the parser flaw except 2.5.4"
    b = one_claim("supported", (1, 2), text=claim, question="Is numpy 2.5.5 secure?")
    ok, bad = judged(b, claim + ".")
    assert [x["text"] for x in ok] == [claim + "."] and bad == []
    for text in ("Every NumPy release is patched against the parser flaw.",          # the exception left out
                 "Every NumPy release is patched against the parser flaw in 2.5.4.",  # and inverted
                 "NumPy is patched against the parser flaw.",
                 "Every NumPy release except 2.5.4 is patched against the parser flaw."):   # reordered: not whole
        ok, bad = judged(b, text)
        assert ok == [], text
        assert bad[0]["reasons"] == [reasoning.RESTRICTION_LOST.format(claim="C1", words="except")], text


def test_the_restriction_rule_reads_the_claims_words_not_its_case_punctuation_or_reference_marks():
    b = one_claim("insufficient", (3,), text="The bridge is open to traffic, except on Sundays [2].",
                  question="Is the bridge open?")
    ok, bad = judged(b, "the bridge is open to traffic except on Sundays")
    assert bad == [] and ok[0]["text"] == "the bridge is open to traffic except on Sundays, according to one source."
    assert reasoning._holds_whole(["a", "b", "c", "d"], ["b", "c"]) and not reasoning._holds_whole(["a", "b"], [])
    assert not reasoning._holds_whole(["a", "b", "x", "c"], ["b", "c"])       # unbroken
    assert not reasoning._holds_whole(["a", "c", "b"], ["b", "c"])            # in order


def test_a_refused_sentence_still_counts_for_the_sentences_after_it():
    """A refusal changes one verdict only: the sources of the refused sentence are counted as cited, exactly as
    when 0.7.0 accepted it, so "another source" in a later sentence is judged as it always was."""
    b = {"question": "Is the bridge open?", "verdict": None,
         "claims": [{"id": "C1", "state": "insufficient", "sources": [1], "published": None, "event": None,
                     "text": "The bridge is open to traffic except on Sundays"},
                    {"id": "C2", "state": "insufficient", "sources": [1], "published": None, "event": None,
                     "text": "The toll is 4 dollars"}],
         "sources": [{"id": 1, "title": "Source 1", "published": "undated"}]}
    composed = reasoning.compose({"sentences": [{"claims": ["C1"], "text": "The bridge is open to traffic."},
                                                {"claims": ["C2"], "text": "Another source says the toll is 4 "
                                                                           "dollars."}]}, b)
    ok, bad = reasoning.guard(composed, b)
    assert ok == [] and len(bad) == 2
    assert bad[0]["reasons"][0].startswith("does not hold C1 whole")
    assert any("another source" in r for r in bad[1]["reasons"])          # as 0.7.0 judged it after accepting the first


def test_a_caveat_of_the_closed_grammar_is_never_refused_for_a_restriction():
    b = one_claim("insufficient", (3,), text="NumPy 2.5.5 is secure unless built with the legacy flag",
                  question="Is numpy 2.5.5 secure?")
    text = "The evidence does not establish whether numpy 2.5.5 is secure."
    assert reasoning.insufficiency(text, b["question"])
    assert reasoning._refusals(text, b["claims"], b) == []
