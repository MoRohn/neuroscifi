"""What the self-critique judges: only sentences that do more than restate their cited claims (reasoning.restates).

Measured on the pipeline benchmark (docs/quality/evidence/accel_after_pipeline.json): the critique call was 42 % of a
research task, could remove nothing the guard had accepted, and 43 of its 46 marks sat on sentences that are a cited
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
    CLAIM, CLAIM[:-1], "  the build   times dropped by 30 percent in our tests. ",
    "Yes, the build times dropped by 30 percent in our tests.",
    "According to one source, the build times dropped by 30 percent in our tests.",
    "The build times dropped by 30 percent in our tests, according to a report.",
])
def test_what_compose_normalises_away_does_not_make_a_sentence_the_models_own(written):
    """Case, spacing, the final stop, a leading answer word and the model's own attribution are replaced by code:
    the composed sentence is the same one, so it is a restatement."""
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


# ---- restates: several cited claims --------------------------------------------------------------------------------

def test_every_cited_claim_joined_by_a_listed_conjunction_is_a_restatement():
    """Live (benchmark task F02): the model joined two cited claims with ", and" and the code appended the hedge of
    the weaker state. Each claim once, either order, a closed list of joins; nothing else between them."""
    b = two_claims()
    a, c = (x["text"][:-1] for x in b["claims"])
    tail = ", according to the cited sources, though the sources disagree."
    live = kept(b, (f"{a}, and {c[0].lower()}{c[1:]}.", ["C1", "C2"]))
    assert live[0]["text"] == f"{a}, and {c[0].lower()}{c[1:]}{tail}" and reasoning.restates(live[0], b)
    for first, second in itertools.permutations((a, c)):
        for join in reasoning._RESTATE_JOINS:
            assert reasoning.restates(item(first + join + second + tail, b, ("C1", "C2")), b), join
        for glue in (" because ", ", so ", " but ", ", which means ", " and therefore ", " or "):
            assert not reasoning.restates(item(first + glue + second + tail, b, ("C1", "C2")), b), glue


def test_a_claim_cited_but_not_restated_is_judged():
    """Two claims cited, one restated: the attribution counts sources whose claim the sentence does not say."""
    b = two_claims()
    a, c = (x["text"][:-1] for x in b["claims"])
    tail = ", according to the cited sources, though the sources disagree."
    assert not reasoning.restates(item(a + tail, b, ("C1", "C2")), b)
    assert not reasoning.restates(item(c + ", according to the cited sources.", b, ("C1", "C2")), b)
    assert not reasoning.restates(item(f"{a}, and {a}, and {c}{tail}", b, ("C1", "C2")), b)     # one claim twice
    assert reasoning.restates(item(a + ", according to one source, though the sources disagree.", b, ("C1",)), b)


def test_three_claims_and_the_same_words_from_two_claims():
    b = two_claims("supported", "supported")
    b["claims"].append({"id": "C3", "state": "supported", "sources": [2], "published": None, "event": None,
                        "text": "Its basin covers 9,800 square kilometres."})
    a, c, d = (x["text"][:-1] for x in b["claims"])
    assert reasoning.restates(item(f"{a}; {d}; {c}.", b, ("C1", "C2", "C3")), b)
    assert reasoning.restates(item(f"{c}, and {a}, and {d}.", b, ("C1", "C2", "C3")), b)
    assert not reasoning.restates(item(f"{a}; {d}; {c}.", b, ("C1", "C2")), b)      # says more than it cites
    assert not reasoning.restates(item(f"{a}, {d} and {c}.", b, ("C1", "C2", "C3")), b)   # a join not in the list
    b["claims"][1]["text"] = b["claims"][0]["text"]                # two sources carrying the same words
    assert reasoning.restates(item(f"{a}.", b, ("C1", "C2")), b)


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
