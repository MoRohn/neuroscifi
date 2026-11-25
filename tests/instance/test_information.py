"""ADR-051 (lane I): the exact expected information gain shown beside decisions, the opt-in rule version
information-v1, and habitual versus deliberate decisions."""
from math import comb, log2

import pytest

from neuroscifi.instance import beliefs, executive, information, store
from neuroscifi.instance.errors import ValidationError

from conftest import T0
from test_executive import Journal, pinned_with_ambiguous_partner


def _h(p):
    return 0.0 if p in (0.0, 1.0) else -(p * log2(p) + (1 - p) * log2(1 - p))


def expected_partner_gain(n0, n1, k=information.READINGS, e=0.1):
    """Independent of the code under test: a partner's convention bit appears in no other factor, so the reading
    relation's posterior is P(r=1) = 1 / (1 + (e/(1-e))^(n1-n0)), and k more readings carry
    H(mixture of two binomials) - k h(e) bits about it."""
    q = 1.0 / (1.0 + (e / (1 - e)) ** (n1 - n0))
    hy = 0.0
    for j in range(k + 1):
        p = q * (1 - e) ** j * e ** (k - j) + (1 - q) * e ** j * (1 - e) ** (k - j)
        hy -= comb(k, j) * p * log2(p)
    return hy - k * _h(e)


@pytest.mark.parametrize("readings", [(0,), (0, 1, 0), (1, 1), (0, 1, 0, 1)])
def test_partner_gain_is_exact(instance, readings):
    db, _ = instance
    j = Journal(db)
    for root in ("ref-1", "ref-2", "ref-3"):
        j.add("reference", root, 0)
        j.add("camera", "cam-a", 0)
    for r in readings:
        j.add("word", "ana", r)
    rows = {r["subject"]: r for r in information.gains(db, "desk")["candidates"]}
    n1 = sum(readings)
    assert rows["partner:ana"]["eig_bits"] == pytest.approx(expected_partner_gain(len(readings) - n1, n1), abs=1e-6)


def test_more_references_teach_less_about_the_mechanism(instance):
    db, _ = instance
    j = Journal(db)
    j.add("camera", "cam-a", 0)
    gains = []
    for root in ("ref-1", "ref-2", "ref-3", "ref-4"):
        j.add("reference", root, 0)
        gains.append({r["subject"]: r for r in information.gains(db, "desk")["candidates"]}["reference"]["eig_bits"])
    assert gains == sorted(gains, reverse=True) and gains[0] > gains[-1] > 0


def test_a_partner_whose_words_are_coin_flips_scores_low(instance):
    db, _ = instance
    j = Journal(db)
    for root in ("ref-1", "ref-2", "ref-3"):
        j.add("reference", root, 0)
    for r in (0, 1) * 6:
        j.add("word", "noisy", r)
    j.add("word", "bo", 1)
    j.add("word", "bo", 1)
    rows = {r["subject"]: r for r in information.gains(db, "desk")["candidates"]}
    noisy, bo = rows["partner:noisy"], rows["partner:bo"]
    assert noisy["eig_bits"] > bo["eig_bits"]                  # the belief model alone would ask the noisy partner
    assert noisy["p_informative"] < 0.01 < 0.5 < bo["p_informative"]
    assert noisy["score"] < bo["score"]


def test_the_display_never_changes_the_decision(instance):
    db, _ = instance
    pinned_with_ambiguous_partner(db)
    shown = executive.decide(db, "desk", T0)
    store.set_config(db, {"decision_information": False}, T0)
    plain = executive.decide(db, "desk", T0)
    assert {k: shown[k] for k in ("decision", "scope", "reason")} == {k: plain[k] for k in ("decision", "scope",
                                                                                           "reason")}
    assert shown["rule"] == plain["rule"] == "rules-v1"
    assert "information" in shown and "information" not in plain and "processing" not in plain
    assert shown["information"]["candidates"][0]["score"] >= shown["information"]["candidates"][-1]["score"]


def test_information_v1_is_opt_in_validated_and_recorded(instance):
    db, _ = instance
    assert store.config(db).executive_rule == "rules-v1"
    with pytest.raises(ValidationError):
        store.set_config(db, {"executive_rule": "information-v2"}, T0)
    store.set_config(db, {"executive_rule": "information-v1"}, T0)
    row = db.execute("SELECT effects FROM deletion_log WHERE target_kind='setting' AND target_id='executive_rule'"
                     ).fetchone()
    assert '"to": "information-v1"' in row[0]
    assert store.InstanceConfig.from_json('{"executive_rule": "no-such-rule"}').executive_rule == "rules-v1"
    with store.write(db):
        db.execute("UPDATE meta SET value=json_set(value,'$.executive_rule','no-such-rule') WHERE key='config'")
    assert store.config(db).executive_rule == "rules-v1"
    assert any("executive_rule" in p and "rules-v1" in p for p in store.setting_problems(db))


def test_information_v1_asks_the_informative_partner_not_the_noisy_one(instance):
    db, _ = instance
    j = Journal(db)
    for root in ("ref-1", "ref-2", "ref-3"):
        for _ in range(3):
            j.add("reference", root, 0)
    j.add("camera", "cam-a", 0)
    j.add("camera", "cam-a", 0)
    j.add("camera", "cam-a", 0)
    for r in (0, 1) * 4:
        j.add("word", "noisy", r)
    j.add("word", "bo", 1)
    assert executive.decide(db, "desk", T0)["scope"] == "partner:noisy"          # rules-v1: most ambiguous partner
    store.set_config(db, {"executive_rule": "information-v1"}, T0)
    out = executive.decide(db, "desk", T0, record=True)
    assert out["rule"] == "information-v1" and out["decision"] == "ask" and out["scope"] == "partner:bo"
    assert out["reason"].startswith("information-v1:") and "P(informative)" in out["reason"]
    assert executive.pending(db)[0]["scope"] == "partner:bo"


def test_information_v1_keeps_rules_one_to_four(instance):
    db, _ = instance
    j = Journal(db)
    j.baseline()
    for k in range(6):
        j.add("camera", "cam-a", 1)
        j.add("word", "ana", 0)
        j.add("reference", f"ref-{k % 3}", 0)
    before = executive.decide(db, "desk", T0)
    store.set_config(db, {"executive_rule": "information-v1"}, T0)
    after = executive.decide(db, "desk", T0)
    assert (before["decision"], before["scope"], before["reason"]) == (after["decision"], after["scope"],
                                                                       after["reason"])
    assert after["decision"] == "ask" and after["scope"] == "camera:cam-a"


def test_information_v1_still_honours_the_ask_budget(instance):
    db, _ = instance
    store.set_config(db, {"executive_rule": "information-v1", "ask_budget_per_epoch": 0}, T0)
    j = Journal(db)
    for root in ("ref-1", "ref-2", "ref-3"):
        j.add("reference", root, 0)
    j.add("word", "bo", 1)
    out = executive.decide(db, "desk", T0, record=True)
    assert out["decision"] == "defer" and "ask budget exhausted" in out["reason"]
    assert executive.pending(db) == []


def test_information_v1_acts_when_nothing_is_worth_asking(instance):
    db, _ = instance
    store.set_config(db, {"executive_rule": "information-v1"}, T0)
    Journal(db).baseline()
    out = executive.decide(db, "desk", T0)
    assert out["decision"] == "act" and "no question reaches" in out["reason"]


def test_habitual_or_deliberate_and_when_the_habit_stops_applying(instance):
    db, _ = instance
    j = Journal(db)
    j.baseline()
    out = executive.decide(db, "desk", T0, record=True)
    assert out["processing"]["mode"] == "deliberate"
    beliefs.ensure_tables(db)
    with store.write(db):
        db.execute("INSERT INTO epoch_links(epoch,source_epoch,segments,pending_id,created_at) "
                   "VALUES('desk','lab','{}',1,?)", (T0 + 1,))
    assert information.processing(db, "desk")["mode"] == "habitual"
    gone = information.processing(db, "desk", {"suspected": True, "scope": "camera:cam-a", "since_seq": 7})
    assert gone["habit_applies"] is False and "no longer apply" in gone["why"]
    executive.decide(db, "desk", T0 + 2, record=True)
    report = information.report(db)
    assert report["decisions"] == 2 and report["habitual"] == 1 and report["habitual_share"] == 0.5
    assert report["linked_epochs"] == 1


def test_the_model_check_holds_on_thousands_of_readings():
    """Thousands of readings underflow the plain likelihoods (0 / 0); the check is computed in logs."""
    assert information._p_informative({0: (9000, 1000)}) == pytest.approx(1.0)
    assert information._p_informative({0: (5000, 5000)}) == pytest.approx(0.0, abs=1e-12)
