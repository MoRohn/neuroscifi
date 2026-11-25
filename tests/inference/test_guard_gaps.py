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
    # review round 2
    ("Every release is patched provided the option is set", "provided"),
    ("Every release is patched on the condition that the option is set", "on the condition that"),
    ("Every release is patched assuming the option is set", "assuming"),
    ("Every release is patched subject to the option being set", "subject to"),
    ("Every release is patched given that the option is set", "given that"),
    ("Every release is patched in case the option is set", "in case"),
    ("Every release is patched in the event that the option is set", "in the event that"),
    ("Every release is patched in the event of a reboot", "in the event of"),
    ("Every release is patched save 2.5.4", "save"),
    ("Every release is patched besides 2.5.4", "besides"),
    ("Every release is patched with 2.5.4 excepted", "excepted"),
    ("Every release but 2.5.4 is patched", "but"),
    ("All versions save the oldest are patched", "save"),
    # remaining items: exceptions written as a negated phrase
    ("The flaw is fixed for all users not on Windows", "not on"),
    ("The patch applies to every release not built from source", "not built"),
    ("Every account not using two-factor login was locked", "not using"),
    ("All customers who are not on the legacy plan get the refund", "not on"),
    ("The fee is waived for any student not yet enrolled", "not yet enrolled"),
    ("All the tickets not sold by Friday go on sale again", "not sold"),
    ("Every test passed, not counting the flaky network tests", "not counting"),
    ("All rights not expressly granted are reserved", "not expressly granted"),
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
    "The census data provided by Tessaly Statistics counted 248,000 residents",  # review round 2
    "The update helps save memory on every device",
    "Besides, the vendor published version 4",
    "The vendor was given the patch in May",
    "Every release shipped on time, but the docs lagged behind",               # "but" too far from "every"
    # remaining items: a "not" that negates a verb, or adds, excepts nothing
    "All NumPy releases are not affected by the parser flaw",
    "Each user did not receive the update in May",
    "Every NumPy release, not only 2.5.5, is patched against the flaw",
    "Not all users on Windows are affected by the flaw",
    "The flaw is not fixed for all users",
    "All of the fixes, not just the parser fix, landed in 2.5.5",
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


# ---- letter case ---------------------------------------------------------------------------------------------------

def claims_of(*texts):
    return [{"id": f"C{i}", "text": t} for i, t in enumerate(texts, 1)]


@pytest.mark.parametrize("claim, sentence, word, theirs", [
    ("Exposure to CO rose in 2025", "Exposure to Co rose in 2025.", "Co", "CO"),                 # another element
    ("The US team shipped the patch on 3 May 2026", "The us team shipped the patch.", "us", "US"),
    ("The US team shipped the patch on 3 May 2026", "The patch shipped on 3 may 2026.", "may", "May"),
    ("Polish regulators fined the firm", "Polish regulators fined the Firm.", "Firm", "firm"),
    ("iOS 19 ships with the eSIM transfer tool", "iOS 19 ships with the ESIM transfer tool.", "ESIM", "eSIM"),
    ("NASA signed the Lumen-7 agreement", "Nasa signed the Lumen-7 agreement.", "Nasa", "NASA"),  # more than a first letter
    ("NASA signed the Lumen-7 agreement", "NASA signed the lumen-7 agreement.", "lumen", "Lumen"),
    ("Turkey raised tariffs on poultry", "Turkey raised tariffs on turkey.", "turkey", "Turkey"),  # a second word
])
def test_a_word_of_the_claims_in_another_case_is_a_change(claim, sentence, word, theirs):
    assert reasoning._case_changed(sentence, claims_of(claim), "q") == [
        reasoning.CASE_CHANGED.format(word=word, theirs=theirs)]


@pytest.mark.parametrize("claim, sentence, question", [
    ("Exposure to CO rose in 2025", "In 2025 exposure to CO rose.", "q"),            # the claim's first word, moved
    ("Polish regulators fined the firm", "polish regulators fined the firm.", "q"),  # the sentence's first word
    ("The IT department said it moved in 2025", "It moved in 2025.", "q"),           # both forms are the claim's
    ("The parser flaw affects NumPy 2.5.4", "The parser flaw affects numpy 2.5.4.", "Is numpy 2.5.4 affected?"),
    ("The lighthouse is open from May to September", "The lighthouse is open from May to September, but this may be "
     "out of date.", "q"),                                                           # a function word of its own
    ("Zephyr Labs Ships Compiler 4.2 With Faster Linking", "Zephyr Labs ships compiler 4.2 with faster linking.", "q"),
    ('The mayor said: "Rain delayed the launch"', "The mayor said that rain delayed the launch.", "q"),
    ("The U.S. team shipped it", "The u.s. team shipped it.", "q"),                  # single letters are not judged
    ("The tower is 72 metres tall", "The tower is 72 metres tall, according to one source.", "q"),
    ("Open Source tools found the flaw", "Open Source tools found the flaw, according to one source.", "q"),
])
def test_what_the_case_rule_does_not_judge(claim, sentence, question):
    assert reasoning._case_changed(sentence, claims_of(claim), question) == []


def test_the_case_rule_reads_claims_only_and_never_the_codes_own_attribution():
    """A source's title is no evidence of how a word is written ("numpy | Snyk" beside a kept live sentence about
    NumPy), and the words compose() appends ("according to one source") are code's, whatever case a claim gives
    the same words."""
    b = {"question": "q", "verdict": None, "sources": [{"id": 1, "title": "numpy | Snyk", "published": "undated"}],
         "claims": [{"id": "C1", "state": "insufficient", "sources": [1], "published": None, "event": None,
                     "text": "One Source reported that NumPy 2.5.5 fixes the parser flaw"}]}
    ok, bad = judged(b, "NumPy 2.5.5 fixes the parser flaw.")
    assert bad == [] and ok[0]["text"] == "NumPy 2.5.5 fixes the parser flaw, according to one source."
    assert reasoning._own_words(ok[0]["text"], "q") == "NumPy 2.5.5 fixes the parser flaw"
    ok, bad = judged(b, "numpy 2.5.5 fixes the Parser flaw.")       # the title's "numpy" excuses nothing
    assert ok == [] and bad[0]["reasons"] == [reasoning.CASE_CHANGED.format(word="numpy", theirs="NumPy"),
                                              reasoning.CASE_CHANGED.format(word="Parser", theirs="parser")]


def test_a_sentence_can_be_refused_for_a_restriction_and_for_a_capital_at_once():
    b = one_claim("supported", (1, 2), text="The US patch covers every model except the 2019 series",
                  question="What does the patch cover?")
    ok, bad = judged(b, "The us patch covers every model.")
    assert ok == [] and bad[0]["reasons"] == [
        reasoning.RESTRICTION_LOST.format(claim="C1", words="except"),
        reasoning.CASE_CHANGED.format(word="us", theirs="US")]


# ---- characters a reader does not see --------------------------------------------------------------------------

RLO, PDF, ZWSP = "‮", "‬", "​"


def test_a_claim_that_holds_a_character_nobody_sees_is_not_cited():
    """A direction override reorders what is shown ("<RLO>72<PDF>" reads 27). The sentence is read without such
    characters, so 0.7.0 compared a clean sentence with a claim that is not what it looks like, and accepted it
    wherever the hidden character stood between two words."""
    b = one_claim("supported", (1, 2), text=f"The release is {RLO}elbats{PDF} on Linux",
                  question="Is the release stable?")
    for text in ("The release is elbats on Linux.", f"The release is {RLO}elbats{PDF} on Linux."):
        ok, bad = judged(b, text)                              # shown to a reader as "stable"; 0.7.0 accepted both
        assert ok == [] and bad[0]["reasons"] == [
            reasoning.UNSEEN_IN_CLAIM.format(claim="C1", points="U+202C, U+202E")], text
    clean = one_claim("supported", (1, 2), text="The release is stable on Linux", question="Is the release stable?")
    ok, bad = judged(clean, f"The release is {RLO}stable{PDF} on Linux.")     # the model's own controls: removed
    assert bad == [] and ok[0]["text"] == "The release is stable on Linux."


def test_only_the_cited_claim_counts_and_white_space_is_not_hidden():
    b = {"question": "How tall is the tower?", "verdict": None,
         "claims": [{"id": "C1", "state": "supported", "sources": [1, 2], "published": None, "event": None,
                     "text": "The tower is 72 metres tall and stands in Tolvane"},
                    {"id": "C2", "state": "supported", "sources": [1, 2], "published": None, "event": None,
                     "text": f"The tower was finished in 1901{ZWSP}"}],
         "sources": [{"id": n, "title": f"Source {n}", "published": "undated"} for n in (1, 2)]}
    ok, bad = judged(b, "The tower is 72 metres tall.")                # a no-break space, a line separator: seen
    assert bad == [] and len(ok) == 1
    ok, bad = judged(b, "The tower was finished in 1901.", ["C2"])
    assert ok == [] and bad[0]["reasons"] == [reasoning.UNSEEN_IN_CLAIM.format(claim="C2", points="U+200B")]
    assert reasoning._unseen_in_claims([{"id": "C9", "text": "plain"}]) == []


# ---- a sentence that is one cited claim word for word ----------------------------------------------------------------

def test_an_exact_restatement_is_not_rejected_by_the_anchor_rule_alone():
    """"The flaw is fixed in versions 2 and 3." for that very claim: 0.7.0's anchor rule read the claim's own "and 3"
    as tying 3 to what the claim says of 2, and a faithful sentence was lost."""
    claim = "The flaw is fixed in versions 2 and 3"
    b = one_claim("supported", (1, 2), text=claim)
    ok, bad = judged(b, claim + ".")
    assert bad == [] and [x["text"] for x in ok] == [claim + "."]
    for text in ("The flaw is fixed in versions 3 and 2.", "The flaw is fixed in version 3.",
                 "The flaw is fixed in Versions 2 and 3."):
        ok, bad = judged(b, text)
        assert ok == [], text


def test_an_exact_restatement_with_another_fault_keeps_every_reason():
    b = one_claim("insufficient", (3,), text="NumPy 2.5.4 and 2.5.5 are verified secure by the maintainers")
    ok, bad = judged(b, "NumPy 2.5.4 and 2.5.5 are verified secure by the maintainers.")
    assert ok == [] and bad[0]["reasons"][0] == "calls an unverified claim verified or confirmed"
    assert any(r.startswith(reasoning.ANCHOR_TIED) for r in bad[0]["reasons"])          # as 0.7.0 listed them


def test_the_exemption_needs_exactly_one_cited_claim_and_its_own_sources():
    b = {"question": "Is numpy 2.5.5 secure?", "verdict": None,
         "claims": [{"id": "C1", "state": "supported", "sources": [1, 2], "published": None, "event": None,
                     "text": "The flaw is fixed in versions 2 and 3"},
                    {"id": "C2", "state": "supported", "sources": [1, 2], "published": None, "event": None,
                     "text": "Version 4 is the latest release"}],
         "sources": [{"id": n, "title": f"Source {n}", "published": "undated"} for n in (1, 2)]}
    ok, bad = judged(b, "The flaw is fixed in versions 2 and 3.", ["C1", "C2"])
    assert ok == [] and any(r.startswith(reasoning.ANCHOR_TIED) for r in bad[0]["reasons"])
    ok, bad = reasoning.guard([{"text": "The flaw is fixed in versions 2 and 3.", "sources": [1], "claims": ["C1"]}],
                              b)
    assert ok == []                                     # one of its two sources: not the claim as compose() cites it


# ---- the sentence's own attribution with further words ---------------------------------------------------------------

@pytest.mark.parametrize("after", [
    "by Reuters", "from the NumPy security team", "by the Ostra Space Agency", "from Tessaly Statistics",
    "by a Reuters news desk", "from the Bank of England", "by The New York Times", "from ACME and Sons",
])
def test_words_that_only_name_the_source_are_removed_as_0_7_0_removed_them(after):
    assert reasoning._names_the_source(after)


@pytest.mark.parametrize("after", [
    "by the operator",                                      # names nobody
    "except in versions 2 and 3", "from PyPI unless pinned", "by Ostra Unless Flooded",     # restrictions
    "for adults", "by the Council in no case", "by Parliament and was vetoed", "by Tolvane Transit until further",
    "from 2025", "from NumPy Two", "from Linux Users Only", "by NOT The City", "from Reuters, Monday",
    "of its lower course", "that the vendor disputes", "by Reuters denied", "by Reuters reportedly",
    "from Bank of", "by Ana Ruiz Jose Luis Maria Carmen Lopez",           # seven words
    "from ACME & Sons",
    "by Orbix Labs Retracted", "by Orbix Labs Withdrawn", "from the Orbix Archive Superseded",   # review round 2
    "by Orbix Labs Disputed", "by Orbix Labs DEBUNKED", "by the Draft Orbix Board",
    "from Windows Users", "from Linux Administrators", "by Enterprise Customers",   # remaining items: a group of
    "from Python Developers", "by Local Residents", "by Citizens Advice",             # people, source or scope                                                   # a sign between words: refused, kept simple
    "",
])
def test_anything_else_after_the_source_word_is_not_a_name(after):
    assert not reasoning._names_the_source(after)


def test_a_sentence_that_only_names_its_source_further_is_shown_as_0_7_0_showed_it():
    b = one_claim("insufficient", (3,), text="The Tolvane Lighthouse is 41 metres tall",
                  question="How tall is the lighthouse?")
    written = "The Tolvane Lighthouse is 41 metres tall according to a report by Reuters."
    items = reasoning.compose({"sentences": [{"claims": ["C1"], "text": written}]}, b)
    assert items == [{"text": "The Tolvane Lighthouse is 41 metres tall, according to one source.", "sources": [3],
                      "claims": ["C1"]}]
    ok, bad = reasoning.guard(items, b)
    assert bad == [] and [x["text"] for x in ok] == ["The Tolvane Lighthouse is 41 metres tall, according to one "
                                                     "source."]
    # the source word's own letters are not words of their own: "according to document|ation by Orbix Labs"
    assert reasoning._cut_with_attribution("Orbix supports encryption at rest according to documentation by "
                                           "Orbix Labs.") is None
    assert reasoning._cut_with_attribution("Orbix supports encryption at rest according to documentation by "
                                           "Orbix Labs since 3.") is not None


# ---- reasons in one order ------------------------------------------------------------------------------------------

def test_a_reason_that_lists_words_lists_them_in_the_same_order_in_every_process():
    """The role-swap reason listed its verbs in the order of a set, which varies from one Python process to the
    next (hash randomisation): the same sentence got differently worded reasons at 0.7.0."""
    import os
    import subprocess
    import sys
    code = ("from neuroscifi.webcortex import reasoning as r\n"
            "print(r._unentailed('Ben hired Ana, Dan promoted Carla, Finn trained Eve and Hal funded Gus.', "
            "[{'id': 'C1', 'state': 'supported', 'sources': [1], 'text': "
            "'Ana hired Ben, Carla promoted Dan, Eve trained Finn and Gus funded Hal'}], []))")
    src = str(__import__("pathlib").Path(reasoning.__file__).resolve().parents[2])
    seen = set()
    for seed in ("1", "2", "3", "4", "5", "6"):
        done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True,
                              env=dict(os.environ, PYTHONHASHSEED=seed, PYTHONPATH=src))
        seen.add(done.stdout)
    assert len(seen) == 1, seen
    assert "changes who does what in its cited claims: hired, promoted, trained" in seen.pop()


# ---- a passive clause that drops or swaps its agent ------------------------------------------------------------------

@pytest.mark.parametrize("claim, sentence, verb, subject", [
    ("NumPy 2.5.4 was replaced by NumPy 2.5.5 after the flaw", "NumPy 2.5.5 was replaced after the flaw.",
     "replaced", "2.5.5 numpy"),
    ("Acme Corp was acquired by Globex in 2025", "Globex was acquired in 2025.", "acquired", "globex"),
    ("Mallory was robbed by Trent", "Trent was robbed, according to the police report by Mallory.", "robbed", "trent"),
    ("The vault was broken by the Kestrel crew", "The Kestrel crew was broken.", "broken", "crew kestrel"),
])
def test_a_passive_whose_subject_is_the_claims_agent_is_refused(claim, sentence, verb, subject):
    assert reasoning._role_swaps_refused(sentence, [{"id": "C1", "text": claim}]) == [
        reasoning.PASSIVE_SWAP.format(verb=verb, claim="C1", subject=subject)]


@pytest.mark.parametrize("claim, sentence", [
    ("NumPy 2.5.4 was replaced by NumPy 2.5.5 after the flaw", "NumPy 2.5.4 was replaced after the flaw."),
    ("NumPy 2.5.4 was replaced by NumPy 2.5.5 after the flaw", "NumPy 2.5.4 was replaced by NumPy 2.5.5."),
    ("Acme Corp was acquired by Globex in 2025", "Acme Corp was acquired in 2025, according to one source."),
    ("The library was audited by Orbix Labs in March", "The library was audited in March."),
    ("Globex acquired Acme Corp in 2025", "Acme Corp was acquired by Globex."),        # active claim: no agent to swap
    ("Mallory was robbed by Trent", "Mallory was robbed, according to the police report by Trent."),
])
def test_a_passive_that_keeps_its_subject_is_not_a_swap(claim, sentence):
    assert reasoning._role_swaps_refused(sentence, [{"id": "C1", "text": claim}]) == []


def test_the_passive_swap_is_refused_through_the_answer_path():
    b = one_claim("supported", (1, 2), text="Mallory was robbed by Trent", question="Who was robbed?")
    ok, bad = judged(b, "Trent was robbed.")
    assert ok == [] and bad[0]["reasons"] == [
        reasoning.PASSIVE_SWAP.format(verb="robbed", claim="C1", subject="trent")]
    ok, bad = judged(b, "Mallory was robbed.")
    assert bad == [] and len(ok) == 1



# ---- an active claim restated with its roles reversed (last review round) -----------------------------------------

@pytest.mark.parametrize("claim, sentence, template, verb, subject", [
    ("Globex acquired Acme Corp in 2025", "Globex was acquired by Acme Corp.", "PASSIVE_SWAP", "acquired", "globex"),
    ("Globex acquired Acme Corp in 2025", "Globex was acquired in 2025.", "PASSIVE_SWAP", "acquired", "globex"),
    ("Globex acquired Acme Corp in 2025", "Globex got acquired in 2025.", "PASSIVE_SWAP", "acquired", "globex"),
    ("Globex acquired Acme Corp in 2025", "Globex was quickly acquired.", "PASSIVE_SWAP", "acquired", "globex"),
    ("Globex acquired Acme Corp in 2025", "It was Globex that was acquired.", "PASSIVE_SWAP", "acquired", "globex"),
    ("Globex acquired Acme Corp in 2025", "It was Acme Corp that acquired Globex.", "ROLE_SWAP", "acquired",
     "acme corp"),
    ("Orbix Labs audited the library in March", "Orbix Labs was audited by the library.", "PASSIVE_SWAP", "audited",
     "labs orbix"),
    ("Mallory was robbed by Trent", "Trent was reportedly robbed.", "PASSIVE_SWAP", "robbed", "trent"),
    ("Mallory was robbed by Trent", "Trent got robbed.", "PASSIVE_SWAP", "robbed", "trent"),
])
def test_roles_reversed_in_any_voice_are_refused(claim, sentence, template, verb, subject):
    found = reasoning._role_swaps_refused(sentence, [{"id": "C1", "text": claim}])
    assert getattr(reasoning, template).format(verb=verb, claim="C1", subject=subject) in found, found


@pytest.mark.parametrize("claim, sentence", [
    ("Globex acquired Acme Corp in 2025", "Acme Corp was acquired by Globex in 2025."),
    ("Globex acquired Acme Corp in 2025", "Acme Corp was acquired in 2025."),
    ("Globex acquired Acme Corp in 2025", "It was Globex that acquired Acme Corp."),
    ("Globex acquired Acme Corp in 2025", "Globex acquired Acme Corp."),
    ("The maintainers patched the parser in May", "The parser was patched by the maintainers in May."),
    # three sentences of the guard's own suite and the benchmark that a first version of the rule refused
    ("PyPI reports numpy latest version 2.5.5 released 2026-11-15.", "numpy 2.5.5 was released on 2026-11-15."),
    ("NumPy 2.5.4 is affected by the parser flaw and NumPy 2.5.5 is not affected.",
     "NumPy 2.5.5 is not affected by the parser flaw, but NumPy 2.5.4 is affected."),
    ("Kestrel is a statically typed programming language created by Ilse Marten in 2019.",
     "Ilse Marten created the Kestrel programming language in 2019."),
])
def test_faithful_voices_are_not_role_swaps(claim, sentence):
    assert reasoning._role_swaps_refused(sentence, [{"id": "C1", "text": claim}]) == []


def test_roles_read_a_reduced_passive_negation_and_a_joined_clause():
    roles = reasoning._roles("Kestrel is a language created by Ilse Marten")
    assert ("created", ["ilse", "marten"], None) in roles
    roles = reasoning._roles("NumPy 2.5.4 is affected by the flaw and NumPy 2.5.5 is not affected")
    assert ("affected", ["flaw"], ["numpy", "2.5.4", "is"]) in roles or ("affected", ["flaw"], ["numpy", "2.5.4"]) \
        in [(v, d, [x for x in t if x != "is"] if t else t) for v, d, t in roles]
    assert any(v == "affected" and t and "2.5.5" in t and "2.5.4" not in t for v, d, t in roles)


def test_a_restriction_after_the_600th_character_of_a_claim_is_read():
    """The rule read a claim cut at 600 characters, so an exception further on was never seen. Production cuts claims
    at 420 (QUOTE_CHARS); a claim reaching the guard by another road is read whole."""
    claim = "The plant supplies drinking water to the whole region" + ", and it has done so reliably" * 22 + \
        ", every household except those on private wells"
    assert len(claim) > 600 and claim.index("except") > 600
    b = one_claim("supported", (1, 2), text=claim, question="Who gets the water?")
    ok, bad = judged(b, "The plant supplies drinking water to the whole region.")
    assert ok == [] and bad[0]["reasons"][0].startswith(reasoning.RESTRICTION_LOST.split("{")[0])
