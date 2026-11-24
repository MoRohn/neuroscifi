"""The answer path gives the verdicts of the attack-tested commit: compose() and the guard of this tree against the
recorded reference (scripts/guard_differential.py, tests/inference/fixtures/guard_reference.json).

Why this exists. The critique scope (reasoning.restates) was added beside the guard, and two later fixes reached
into compose() and the guard's anchor rule. The final security pass found that both moved verdicts: a sentence
inverting "... except 2.5.4 and 2.5.5" was accepted, and a claim's own "according to the official advisory of
evilcorp" was kept in the answer as if it were support. The answer path was put back to what 307b907 does, and this
test holds it there: 1,476 inputs (every argument the guard's own tests of that commit pass to compose() and the
guard, the security review's corpora, and the live benchmark's sentences), each compared in full.

One difference is allowed, and only in the refusing direction: a sentence whose own "according to <source> ..."
would take further words with it is refused (reasoning.CUT_WORDS) where 307b907 showed it without them.
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


@pytest.fixture(scope="module")
def reference():
    return json.loads(gd.REFERENCE.read_text())


@pytest.fixture(scope="module")
def result(reference):
    return gd.differences(reasoning, reference)


def texts(items):
    return [x["text"] for x in items]


def test_the_reference_is_the_attack_tested_commit_and_its_inputs_are_of_three_kinds(reference):
    assert reference["commit"] == "307b9070f973be63b1cdbd8da222da8114bd32d6"
    assert reference["reference_guard_tests"] == "827 passed"          # that commit's own guard tests, while recording
    assert reference["cases_by_origin"] == {"harvested": 1137, "review": 91, "benchmark": 248}
    assert len(reference["cases"]) == 1476
    kinds = {c["kind"] for c in reference["cases"]}
    assert kinds == {"compose", "guard", "answer"}


def test_no_verdict_and_no_accepted_text_differs_from_the_reference(result):
    """Every input: the same composed sentences, the same accepted items in full, the same rejections and reasons;
    or, for the cases counted as stricter, exactly that minus sentences refused under CUT_WORDS."""
    assert result["different"] == [], json.dumps(result["different"][:3], indent=1)[:4000]
    assert result["identical"] + len(result["stricter"]) == result["compared"] == 1476


def test_the_guards_own_attack_sentences_and_the_benchmarks_sentences_are_answered_identically(result):
    """All 1,137 inputs of the attack rounds and all 248 benchmark sentences: identical, no stricter case among
    them. So no sentence the benchmark kept is lost to the one added refusal."""
    assert [d["origin"] for d in result["stricter"] if not d["origin"].startswith("review")] == []
    assert result["identical"] >= 1137 + 248


def test_what_is_stricter_is_a_subset_with_identical_text(result):
    """Each stricter case: every item accepted now was accepted by the reference, identically; whatever is missing
    was refused for the words its attribution would drop; no other rejection changed."""
    mark = reasoning.CUT_WORDS.split("'")[0]
    assert len(result["stricter"]) == 17
    refused = 0
    for d in result["stricter"]:
        now, ref = d["now"], d["reference"]
        assert all(item in ref["accepted"] for item in now["accepted"]), d["case"]
        assert len(now["accepted"]) <= len(ref["accepted"])
        new = [r for r in now["rejected"] if r not in ref["rejected"]
               and gd.comparable({"rejected": [r]}) not in [gd.comparable({"rejected": [x]}) for x in ref["rejected"]]]
        assert all(r["reasons"][0].startswith(mark) and len(r["reasons"]) == 1 for r in new), d["case"]
        assert len(new) == len(ref["accepted"]) - len(now["accepted"])
        refused += len(new)
    assert refused == 16                                # in 13 cases; the other 4 carry the mark on a sentence the
    #                                                     guard rejects for its own reasons, as before


def test_the_differential_reports_a_guard_that_accepts_more(monkeypatch, reference):
    """The test can fail: without the anchor rule, verdicts move and are reported as different, never as stricter."""
    monkeypatch.setattr(reasoning, "_anchor_problems", lambda text, used: [])
    moved = gd.differences(reasoning, reference)
    assert len(moved["different"]) > 20 and {d["origin"].split(":")[0] for d in moved["different"]} >= {"harvested",
                                                                                                         "review"}


def test_the_differential_reports_a_changed_accepted_text(monkeypatch, reference):
    monkeypatch.setitem(reasoning.HEDGES, "insufficient", "as {who} says")
    moved = gd.differences(reasoning, reference)
    assert len(moved["different"]) > 20


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
    hedge is added, so the answer stated it as fact. (The one-version form, "... in 2.5.4.", passes at 307b907 too:
    an older gap in how "except" is read, recorded in the reference and listed as open.)"""
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
