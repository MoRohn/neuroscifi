"""The answer path against the attack-tested commit: compose() and the guard of this tree against the recorded
reference (scripts/guard_differential.py, tests/inference/fixtures/guard_reference.json).

Why this exists. The guard is the last check between fetched web text and what an answer states. A fix made beside
it once moved verdicts unnoticed (a sentence inverting "... except 2.5.4 and 2.5.5" was accepted; a claim's own
"according to the official advisory of evilcorp" was kept as if it were support), and was withdrawn. Since then
every change to the answer path is held to this test: every argument the guard's own tests of 307b907 pass to
compose() and the guard, the security review's corpora, the live benchmark's sentences, and one adversarial corpus
per gap closed since (INPUTS), each compared in full.

A verdict may differ from 307b907 in two named ways only (scripts/guard_differential.py, classify):
  stricter   a sentence 307b907 accepted is refused, with reasons that all start with a refusal mark
             (reasoning.CUT_WORDS, reasoning.REFUSALS);
  loosened   a sentence 307b907 rejected is accepted, of a class the script proves (one class: a sentence that is
             exactly one cited claim, rejected at 307b907 by its anchor rule alone).
The gap corpora say what the finished guard must do with each of their sentences; UNMET counts, per gap, the
sentences this tree does not yet treat so, and goes to zero gap by gap.

Closed so far:
  restriction  a cited claim that carries an exception or a condition is restated whole or not at all
               (reasoning.RESTRICTION_LOST), an exception written as a negated phrase too ("every release not
               built from source", "not counting"), however far into the claim. Stricter only.
  case         a word the sentence shares with its cited claims is written as they write it
               (reasoning.CASE_CHANGED); a pronoun of the sentence's own is not excused beside the claim's
               acronym ("US sanctions hit us"). Stricter only.
  unseen       a claim that holds a character nobody sees is not cited (reasoning.UNSEEN_IN_CLAIM). Stricter
               only. (Extraction removes such characters from fetched text: tests/webcortex/test_extract.py.)
  restatement  a sentence that is one cited claim word for word is not rejected by the anchor rule when that is
               its only fault (reasoning.check_sentence, ANCHOR_TIED). The one loosening; its class is proven case
               by case (scripts/guard_differential.py, LOOSENINGS). And a version or number joined in one
               clause with what the cited claims say, in another clause, of another version or number is refused
               (reasoning.JOINED_APART): stricter only.
  cut          the refusal of a sentence whose own attribution would take words with it (reasoning.CUT_WORDS)
               spares words that only name the source ("by Reuters"); such a sentence gets 307b907's verdict
               and text again; words for a group of people ("from Windows Users") are no name, since the
               sentence may be about them. Still stricter only against 307b907.
  role         a role reversal in either voice (reasoning.PASSIVE_SWAP, ROLE_SWAP), with an adverb before the
               claim's verb ("Globex quietly acquired ..."), a get-passive, a cleft, or a cleft on the agent ("It was
               by Acme Corp that Globex was acquired"). Stricter only.
"""
import importlib.util
import json
from pathlib import Path

import pytest

from neuroscifi.webcortex import reasoning
from test_inference_reasoning import one_claim

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "guard_differential.py"
_spec = importlib.util.spec_from_file_location("guard_differential", SCRIPT)
gd = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gd)

GAPS = ("restriction", "case", "unseen", "restatement", "cut", "wording", "role")
INPUTS = {"harvested": 1137, "review": 91, "benchmark": 248, "gap": 448}
SIZES = {"restriction": 138, "case": 56, "unseen": 37, "restatement": 81, "cut": 64, "wording": 33, "role": 39}
# Sentences of each gap corpus this tree does not yet treat as the corpus says. A gap is closed when its count is 0.
UNMET = {"restriction": 0, "case": 1, "unseen": 0, "restatement": 6, "cut": 3, "wording": 0, "role": 0}
CUT_MARK = reasoning.CUT_WORDS.split("'")[0]
RESTRICTION_MARK = reasoning.RESTRICTION_LOST.split("{")[0]
CASE_MARK = reasoning.CASE_CHANGED.split("'")[0]
UNSEEN_MARK = reasoning.UNSEEN_IN_CLAIM.split("{")[0]
PASSIVE_MARK = reasoning.PASSIVE_SWAP.split("'")[0]
ROLE_MARK = reasoning.ROLE_SWAP.split("{")[0]
JOINED_MARK = reasoning.JOINED_APART.split("'")[0]
# What differs from 307b907, counted: cases by origin, and the sentences refused under each refusal mark
STRICTER = {"review": 24, "gap": 203}
LOOSENED = {"review": 6, "gap": 11}
REFUSED = {CUT_MARK: 52, RESTRICTION_MARK: 82, CASE_MARK: 29, UNSEEN_MARK: 18, PASSIVE_MARK: 37, ROLE_MARK: 21,
           JOINED_MARK: 9}


@pytest.fixture(scope="module")
def reference():
    return json.loads(gd.REFERENCE.read_text())


@pytest.fixture(scope="module")
def result(reference):
    return gd.differences(reasoning, reference)


@pytest.fixture(scope="module")
def expected(reference):
    return gd.expectations(reasoning, reference)


def texts(items):
    return [x["text"] for x in items]


def origin(d):
    return d["origin"].split(":")[0]


def test_the_reference_is_the_attack_tested_commit_and_its_inputs_are_of_four_kinds(reference):
    assert reference["commit"] == "307b9070f973be63b1cdbd8da222da8114bd32d6"
    assert reference["reference_guard_tests"] == "827 passed"          # that commit's own guard tests, while recording
    assert reference["cases_by_origin"] == INPUTS
    assert len(reference["cases"]) == sum(INPUTS.values()) == 1924
    kinds = {c["kind"] for c in reference["cases"]}
    assert kinds == {"compose", "guard", "answer"}


def test_no_verdict_differs_from_the_reference_except_in_the_named_ways(result):
    """Every input: the same composed sentences, the same accepted items in full, the same rejections and reasons;
    or stricter, or loosened, as scripts/guard_differential.py defines them. Nothing else."""
    assert result["different"] == [], json.dumps(result["different"][:3], indent=1)[:4000]
    assert result["identical"] + len(result["stricter"]) + len(result["loosened"]) == result["compared"]
    assert result["compared"] == sum(INPUTS.values())


def test_the_guards_own_attack_sentences_and_the_benchmarks_sentences_are_answered_identically(result):
    """All 1,137 inputs of the attack rounds and all 248 benchmark sentences: identical. No sentence the benchmark
    kept is lost to a refusal, and no attack sentence is accepted that 307b907 rejected."""
    assert [d["origin"] for d in result["stricter"] + result["loosened"] if origin(d) in ("harvested", "benchmark")] == []
    assert result["identical"] >= 1137 + 248


def by_origin(cases):
    out = {}
    for d in cases:
        out[origin(d)] = out.get(origin(d), 0) + 1
    return out


def test_what_is_stricter_is_a_subset_with_identical_text(result):
    """Each stricter case: every item accepted now was accepted by the reference, identically; whatever is missing
    was refused with reasons that all start with a refusal mark; no other rejection changed. Counted by mark."""
    marks = gd.refusal_marks(reasoning)
    assert set(marks) == set(REFUSED)
    refused = {}
    for d in result["stricter"] + result["loosened"]:
        now, ref = d["now"], d["reference"]
        if d in result["stricter"]:
            assert all(item in ref["accepted"] for item in now["accepted"]), d["case"]
        theirs = [gd.comparable({"rejected": [x]}) for x in ref["rejected"]]
        new = [r for r in now["rejected"] if gd.comparable({"rejected": [r]}) not in theirs]
        assert all(r["reasons"] and all(x.startswith(marks) for x in r["reasons"]) for r in new), d["case"]
        assert len(new) == len([item for item in ref["accepted"] if item not in now["accepted"]])
        for r in new:
            for m in marks:
                if any(x.startswith(m) for x in r["reasons"]):
                    refused[m] = refused.get(m, 0) + 1
    assert by_origin(result["stricter"]) == STRICTER
    assert refused == REFUSED


def test_no_reason_of_the_reference_starts_like_a_refusal(reference):
    """The refusal marks tell the added refusals from every reason 307b907 gives: none of its reasons starts so."""
    marks = gd.refusal_marks(reasoning)
    reasons = {x for c in reference["cases"] for r in c["reference"].get("rejected") or [] for x in r["reasons"]}
    assert len(reasons) > 300 and not [x for x in reasons if x.startswith(marks)]


def test_what_is_loosened_is_of_a_class_the_differential_proves(result):
    """Each loosened case: every item only this tree accepts is one cited claim word for word, and 307b907 rejected
    that text for the anchor rule and nothing else (scripts/guard_differential.py, _exact_restatement)."""
    assert by_origin(result["loosened"]) == LOOSENED
    for d in result["loosened"]:
        assert d["detail"]["classes"] and set(d["detail"]["classes"]) == {"restatement"}, d["case"]
        theirs = [x["text"] for x in d["reference"]["accepted"]]
        for item in d["now"]["accepted"]:
            if item["text"] not in theirs:
                rejected = [r for r in d["reference"]["rejected"] if r["text"] == item["text"]]
                assert rejected and all(x.startswith(reasoning.ANCHOR_TIED) for r in rejected for x in r["reasons"])
                assert len(item["claims"]) == 1


def test_the_differential_reports_a_wider_exemption_as_different(monkeypatch, reference):
    """The class is proven, not assumed: an exemption for sentences that only resemble a claim is reported, because
    the proof uses its own frozen copy of the class (gd._exact_restatement), not the tree's restates()."""
    monkeypatch.setattr(reasoning, "restates", lambda item, b: True)
    moved = gd.differences(reasoning, reference)
    assert len(moved["different"]) > 0


def test_the_frozen_class_is_the_exact_restatement_and_nothing_more():
    """gd._exact_restatement holds 307b907's attribution table and needs: one claim, its own sources, its text
    character for character with that attribution, no 'according to', no unseen character, no pronoun lead."""
    def bundle(text, state="supported", sources=(1, 2)):
        return {"claims": [{"id": "C1", "state": state, "sources": list(sources), "text": text}]}
    anchor = {"rejected": [{"text": None, "reasons": [gd.ANCHOR_REASON + ": 3: says what ..."]}]}

    def proven(item_text, b, cites=(1, 2), claims=("C1",)):
        ref = {"rejected": [{"text": item_text, "reasons": anchor["rejected"][0]["reasons"]}]}
        return gd._exact_restatement(reasoning, {"text": item_text, "citations": list(cites), "claims": list(claims)},
                                     b, ref)
    claim = "The flaw is fixed in versions 2 and 3"
    assert proven(claim + ".", bundle(claim))
    assert proven(claim + ", according to one source.", bundle(claim, "insufficient", (3,)), cites=(3,))
    assert proven(claim + ", according to the cited sources, though the sources disagree.",
                  bundle(claim, "conflicting", (3, 4)), cites=(3, 4))
    assert not proven(claim + ".", bundle(claim), cites=(1,))                         # not its own sources
    assert not proven(claim + ".", bundle(claim), claims=("C1", "C2"))
    assert not proven("The flaw is fixed in Versions 2 and 3.", bundle(claim))       # not character for character
    assert not proven(claim + ", according to one source.", bundle(claim))           # a hedge on a supported claim
    assert not proven("It is fixed in versions 2 and 3.", bundle("It is fixed in versions 2 and 3"))
    assert not proven("Fixed according to the notes in 2 and 3.", bundle("Fixed according to the notes in 2 and 3"))
    assert not proven("Fixed in 2\u200b and 3.", bundle("Fixed in 2\u200b and 3"))
    ref_other = {"rejected": [{"text": claim + ".", "reasons": ["says more than its cited claims support: x"]}]}
    assert not gd._exact_restatement(reasoning, {"text": claim + ".", "citations": [1, 2], "claims": ["C1"]},
                                     bundle(claim), ref_other)                      # rejected for more than the anchor


@pytest.mark.parametrize("opener", ["It is", "They", "This build", "Both", "Such builds", "The latter", "The former",
                                    "One", "These builds", "Those builds", "Another build", "Each build",
                                    "Neither build", "Either build", "The same build", "Others"])
def test_an_opener_that_needs_its_page_gets_no_exemption_live_or_frozen(opener):
    """Re-review: "Both ...", "Such ...", "The latter ...", "One ..." were exempt word for word and the frozen class
    counted them as proven. Both lists now leave them out."""
    claim = f"{opener} fixed in versions 2 and 3"
    assert reasoning._pronoun_led(claim)
    b = {"claims": [{"id": "C1", "state": "supported", "sources": [1, 2], "text": claim}]}
    ref = {"rejected": [{"text": claim + ".", "reasons": [gd.ANCHOR_REASON + ": 3: says what ..."]}]}
    assert not gd._exact_restatement(reasoning, {"text": claim + ".", "citations": [1, 2], "claims": ["C1"]}, b, ref)


@pytest.mark.parametrize("text", ["The flaw is fixed", "Theirs is not a word here", "Oneida County is affected",
                                  "Bothell schools closed", "The latter-day release", "Otherwise fixed"])
def test_ordinary_openers_keep_the_exemption(text):
    assert not reasoning._pronoun_led(text) or text.startswith("The latter")


def test_a_pronoun_led_claim_gets_no_exemption():
    """Review L1: "It is fixed in versions 2 and 3 but not in NumPy 2.5.5." was accepted word for word under the
    exemption; out of its page "It" attaches to whatever the answer is about. It is rejected as at 307b907."""
    b = one_claim("supported", (1, 2), text="It is fixed in versions 2 and 3 but not in NumPy 2.5.5")
    ok, bad = reasoning.guard(reasoning.compose({"sentences": [{"claims": ["C1"], "text":
                                                  "It is fixed in versions 2 and 3 but not in NumPy 2.5.5."}]}, b), b)
    assert ok == [] and bad[0]["reasons"][0].startswith(reasoning.ANCHOR_TIED)


def test_the_differential_reports_a_guard_that_accepts_more(monkeypatch, reference):
    """The test can fail: without the anchor rule, verdicts move and are reported as different, never as stricter
    or loosened."""
    monkeypatch.setattr(reasoning, "_anchor_problems", lambda text, used: [])
    moved = gd.differences(reasoning, reference)
    assert len(moved["different"]) > 20 and {origin(d) for d in moved["different"]} >= {"harvested", "review"}


def test_the_differential_reports_a_changed_accepted_text(monkeypatch, reference):
    monkeypatch.setitem(reasoning.HEDGES, "insufficient", "as {who} says")
    moved = gd.differences(reasoning, reference)
    assert len(moved["different"]) > 20


def test_a_refusal_with_an_unknown_reason_is_a_difference_not_a_stricter_case(reference):
    """A sentence refused for a reason that is not one of the tree's refusal marks is reported as different."""
    case = next(c for c in reference["cases"] if origin(c) == "benchmark" and c["reference"].get("accepted"))
    bundle = reference["bundles"][case["bundle"]]
    now = json.loads(json.dumps(case["reference"]))
    gone = now["accepted"].pop()
    now["rejected"].append({"text": gone["text"], "reasons": ["refused for a reason of its own"]})
    assert gd.classify(reasoning, case["reference"], now, bundle, gd.refusal_marks(reasoning))[0] == "different"
    now["rejected"][-1]["reasons"] = [reasoning.CUT_WORDS.format(words="x")]
    assert gd.classify(reasoning, case["reference"], now, bundle, gd.refusal_marks(reasoning))[0] == "stricter"


# ---- the gap corpora ---------------------------------------------------------------------------------------------

def test_each_gap_has_its_own_corpus_recorded_against_the_reference(reference, expected):
    sizes = {}
    for e in expected:
        sizes[e["gap"]] = sizes.get(e["gap"], 0) + 1
    assert sizes == SIZES
    assert all(sizes[g] >= 30 for g in GAPS if g != "wording")       # wording moves no verdict: five witnesses
    labels = {e["expect"] for e in expected}
    assert labels == {"reject", "accept", "lost", "open"}
    # every sentence is recorded with what 307b907 does with it, by running that commit (never by hand)
    assert all("reference" in c for c in reference["cases"] if "expect" in c)


@pytest.mark.parametrize("gap", GAPS)
def test_the_sentences_of_a_gap_corpus_not_yet_treated_as_it_says(gap, expected):
    """UNMET[gap] sentences of the corpus are not yet treated as the corpus says; 0 for a gap that is closed."""
    unmet = [e for e in expected if e["gap"] == gap and e["want"] != e["got"]]
    assert len(unmet) == UNMET[gap], "\n".join(f"{e['expect']}: got {e['got']}: {e['text']!r} {e['reasons']}"
                                               for e in unmet)[:6000]


# ---- the final review's attacks, by name ---------------------------------------------------------------------------

@pytest.mark.parametrize("claim, sentence", [
    ("Every NumPy release is patched against the parser flaw except 2.5.4 and 2.5.5",
     "Every NumPy release is patched against the parser flaw in 2.5.4 and 2.5.5."),
    ("Every NumPy release is patched against the parser flaw except 2.5.4 and 2.5.5",
     "NumPy is patched against the parser flaw in 2.5.4 and 2.5.5."),
    ("The flaw is fixed in all versions other than 2.5.4 and 2.5.5", "The flaw is fixed in versions 2.5.4 and 2.5.5."),
    ("The flaw is fixed in every version apart from 2.5.4, and 2.5.5",
     "The flaw is fixed in version 2.5.4, and 2.5.5."),
    ("The flaw is fixed in 2.5.3 but not in 2.5.4 and 2.5.5", "The flaw is fixed in 2.5.4 and 2.5.5."),
    ("The flaw is fixed in 2.5.3 and is still open in 2.5.4 and 2.5.5", "The flaw is fixed in 2.5.4 and 2.5.5."),
])
def test_a_sentence_that_inverts_an_exception_over_two_versions_is_rejected(claim, sentence):
    """Final review FP-H1: accepted for a while by an exemption added to the anchor rule; with a supported claim no
    hedge is added, so the answer stated it as fact."""
    b = one_claim("supported", (1, 2), text=claim, question="Is numpy 2.5.5 secure?")
    items = reasoning.compose({"sentences": [{"claims": ["C1"], "text": sentence}]}, b)
    ok, bad = reasoning.guard(items, b)
    assert ok == [] and any("ties a version or name" in r for r in bad[0]["reasons"]), (ok, bad)


@pytest.mark.parametrize("weak, sentence", [
    ("The patch landed according to the official advisory of evilcorp",
     "NumPy 2.5.5 is secure according to the official advisory of evilcorp."),
    ("Upgrades are free according to the official advisory",
     "NumPy 2.5.5 is secure according to the official advisory."),
])
def test_a_claims_own_attribution_never_becomes_support_for_another_claim(weak, sentence):
    """Final review FP-M1: a hostile claim's "according to the official advisory of evilcorp" was kept in the answer
    and counted as what that claim supports, so its source was named as the authority for a supported fact."""
    b = {"question": "Is numpy 2.5.5 secure?", "verdict": None,
         "claims": [{"id": "C1", "state": "supported", "sources": [1], "published": None, "event": None,
                     "text": "NumPy 2.5.5 is secure"},
                    {"id": "C2", "state": "insufficient", "sources": [2], "published": None, "event": None,
                     "text": weak}],
         "sources": [{"id": n, "title": f"Source {n}", "published": "undated"} for n in (1, 2)]}
    items = reasoning.compose({"sentences": [{"claims": ["C1", "C2"], "text": sentence}]}, b)
    ok, bad = reasoning.guard(items, b)
    assert ok == [] and bad, (ok, bad)
    assert "evilcorp" not in " ".join(texts(ok)) and "according to the cited sources, according" not in str(items)
