"""The executive's 7-rule policy, the ask budget, pending questions and decision records."""
import pytest

from neuroscifi.instance import evidence, executive, store
from neuroscifi.instance.errors import Conflict, NotFound, ValidationError

from conftest import T0


class Journal:
    def __init__(self, db, epoch="desk"):
        self.db, self.epoch, self.count = db, epoch, 0
        evidence.start_epoch(db, epoch, T0)

    def add(self, channel, root, corrected):
        self.count += 1
        return evidence.observe(self.db, {"id": f"{self.epoch}-{self.count}", "epoch": self.epoch,
                                          "channel": channel, "action": 1, "bit": corrected ^ 1, "root": root},
                                T0)["seq"]

    def baseline(self, rounds=12, references=True):
        for k in range(rounds):
            self.add("camera", "cam-a", 0)
            self.add("word", "ana", 0)
            if references:
                self.add("reference", f"ref-{k % 3}", 0)


def rows(db, table):
    return db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]


def test_rule1_writes_paused_defers_and_writes_nothing(tmp_path):
    db = store.create(tmp_path / "full.sqlite", T0, config=store.InstanceConfig(max_evidence_rows=3))
    try:
        j = Journal(db)
        for _ in range(3):
            j.add("camera", "cam-a", 0)
        version = store.state_version(db)
        out = executive.decide(db, "desk", T0, record=True)
        assert out["decision"] == "defer" and out["pending_id"] is None
        assert "writes are paused" in out["reason"] and "3 rows (limit 3)" in out["reason"]
        assert rows(db, "decisions") == rows(db, "pending") == 0 and store.state_version(db) == version
    finally:
        db.close()


def test_rule2_clear_change_asks_to_calibrate_that_scope(instance):
    db, _ = instance
    j = Journal(db)
    j.baseline()
    for k in range(6):
        j.add("camera", "cam-a", 1)
        j.add("word", "ana", 0)
        j.add("reference", f"ref-{k % 3}", 0)
    out = executive.decide(db, "desk", T0, record=True)
    assert out["decision"] == "ask" and out["scope"] == "camera:cam-a"
    assert "change suspected in camera:cam-a" in out["reason"] and "log(19)=2.94" in out["reason"]
    (question,) = executive.pending(db, epoch="desk")
    assert question["kind"] == "calibrate" and question["scope"] == "camera:cam-a"
    assert question["id"] == out["pending_id"] and out["asks_used"] == 1


def test_rule3_ambiguous_change_tests_reference_when_one_exists(instance):
    db, _ = instance
    j = Journal(db)
    j.baseline()
    for _ in range(6):
        j.add("camera", "cam-a", 1)
    out = executive.decide(db, "desk", T0, record=True)
    assert out["decision"] == "test_reference"
    assert "ambiguous" in out["reason"] and "mechanism" in out["reason"] and "camera:cam-a" in out["reason"]
    assert executive.pending(db)[0]["kind"] == "test_reference" and out["asks_used"] == 0


def test_rule3_ambiguous_change_asks_without_reference_evidence(instance):
    db, _ = instance
    j = Journal(db)
    j.baseline(references=False)
    for _ in range(6):
        j.add("camera", "cam-a", 1)
    out = executive.decide(db, "desk", T0, record=True)
    assert out["decision"] == "ask" and "no reference evidence" in out["reason"]
    assert executive.pending(db)[0]["kind"] == "ask"


def test_rule4_no_learning_evidence_observes(instance):
    db, _ = instance
    evidence.start_epoch(db, "desk", T0)
    evidence.observe(db, {"id": "m", "epoch": "desk", "channel": "camera", "action": 0, "bit": 0,
                          "root": "cam-a", "consent": "memory_only"}, T0)
    out = executive.decide(db, "desk", T0)
    assert out["decision"] == "observe" and out["scope"] is None
    assert "no learning evidence yet (1 stored, 1 memory-only)" in out["reason"]


def test_rule5_unidentifiable_mechanism_tests_reference(instance):
    db, _ = instance
    j = Journal(db)
    j.baseline(rounds=5, references=False)
    out = executive.decide(db, "desk", T0)
    assert out["decision"] == "test_reference" and out["scope"] == "mechanism"
    assert "p_inverted=0.500" in out["reason"]
    assert "try a known mechanical reference from a distinct root" in out["reason"]
    assert out["pending_id"] is None and rows(db, "decisions") == 0  # record=False writes nothing


def pinned_with_ambiguous_partner(db):
    j = Journal(db)
    for root in ("ref-1", "ref-2", "ref-3"):
        j.add("reference", root, 0)
        j.add("camera", "cam-a", 0)
    for corrected in (0, 1, 0, 1):
        j.add("word", "ana", corrected)
    return j


def test_rule6_ambiguous_partner_asks(instance):
    db, _ = instance
    pinned_with_ambiguous_partner(db)
    out = executive.decide(db, "desk", T0)
    assert out["decision"] == "ask" and out["scope"] == "partner:ana"
    assert "partner ana has 4 learning words but p_inverted=0.500" in out["reason"]


def test_rule6_needs_four_words(instance):
    db, _ = instance
    j = Journal(db)
    for root in ("ref-1", "ref-2", "ref-3"):
        j.add("reference", root, 0)
    j.add("word", "ana", 0)
    j.add("word", "ana", 1)
    j.add("word", "ana", 0)
    assert executive.decide(db, "desk", T0)["decision"] == "act"


def test_rule7_resolved_beliefs_act(instance):
    db, _ = instance
    Journal(db).baseline()
    out = executive.decide(db, "desk", T0, record=True)
    assert out["decision"] == "act" and out["pending_id"] is None
    assert "test a new action and check its observed consequence" in out["reason"]
    assert db.execute("SELECT decision, scope, state_version FROM decisions").fetchone() == (
        "act", None, store.state_version(db))
    assert executive.pending(db) == []


def test_pending_rows_are_deduplicated(instance):
    db, _ = instance
    pinned_with_ambiguous_partner(db)
    first = executive.decide(db, "desk", T0, record=True)
    second = executive.decide(db, "desk", T0 + 1, record=True)
    assert first["pending_id"] == second["pending_id"]
    assert rows(db, "pending") == 1 and rows(db, "decisions") == 2
    assert second["asks_used"] == 1
    assert executive.decide(db, "desk", T0 + 2)["pending_id"] == first["pending_id"]


def test_ask_budget_turns_the_fourth_new_question_into_defer(instance):
    db, _ = instance
    j = pinned_with_ambiguous_partner(db)
    for n in range(3):
        out = executive.decide(db, "desk", T0 + n, record=True)
        assert out["decision"] == "ask" and out["asks_used"] == n + 1 and out["ask_budget"] == 3
        executive.resolve(db, out["pending_id"], f"answer {n}", T0 + n)
        for corrected in (0, 1, 0, 1):   # four new words leave Ana ambiguous: a genuinely new question
            j.add("word", "ana", corrected)
    preview = executive.decide(db, "desk", T0 + 9)
    out = executive.decide(db, "desk", T0 + 10, record=True)
    assert preview == out
    assert out["decision"] == "defer" and out["pending_id"] is None and out["asks_used"] == 3
    assert "ask budget exhausted: 3 of 3" in out["reason"] and "partner ana" in out["reason"]
    assert rows(db, "pending") == 3
    assert db.execute("SELECT decision FROM decisions ORDER BY id DESC LIMIT 1").fetchone() == ("defer",)
    other = Journal(db, "garage")  # the budget is per epoch
    for root in ("ref-1", "ref-2", "ref-3"):
        other.add("reference", root, 0)
    for corrected in (0, 1, 0, 1):
        other.add("word", "ana", corrected)
    assert executive.decide(db, "garage", T0)["decision"] == "ask"


def test_an_answered_question_is_not_asked_again_for_the_same_events(instance):
    db, _ = instance
    j = pinned_with_ambiguous_partner(db)
    first = executive.decide(db, "desk", T0, record=True)
    executive.resolve(db, first["pending_id"], "Ana is still deciding", T0 + 1)
    again = executive.decide(db, "desk", T0 + 2, record=True)
    assert again["decision"] == "act" and again["pending_id"] is None and again["asks_used"] == 1
    assert "already answered: partner ana (question #%d answered)" % first["pending_id"] in again["reason"]
    j.add("word", "ana", 0)
    j.add("word", "ana", 1)
    assert executive.decide(db, "desk", T0 + 3)["decision"] == "act"   # two new words: still answered
    j.add("word", "ana", 0)
    j.add("word", "ana", 1)
    out = executive.decide(db, "desk", T0 + 4, record=True)
    assert out["decision"] == "ask" and out["scope"] == "partner:ana" and out["asks_used"] == 2
    assert out["pending_id"] != first["pending_id"]


def test_open_questions_expire_after_seven_days(instance):
    db, _ = instance
    pinned_with_ambiguous_partner(db)
    first = executive.decide(db, "desk", T0, record=True)
    preview = executive.decide(db, "desk", T0 + 7 * 86400 + 1)
    out = executive.decide(db, "desk", T0 + 7 * 86400 + 1, record=True)
    assert out["expired_pending"] == [first["pending_id"]]
    assert preview["pending_id"] is None and out["pending_id"] != first["pending_id"]
    (old,) = [p for p in executive.pending(db, include_resolved=True) if p["id"] == first["pending_id"]]
    assert old["resolution"] == "expired: no answer within 7 days" and old["resolved_at"] == T0 + 7 * 86400 + 1
    assert out["decision"] == "ask" and out["asks_used"] == 2   # nobody answered, so asking again is new


def test_resolve_bumps_teacher_interventions_once(instance):
    db, _ = instance
    pinned_with_ambiguous_partner(db)
    pid = executive.decide(db, "desk", T0, record=True)["pending_id"]
    done = executive.resolve(db, pid, "Ana means action 0", T0 + 5)
    assert done["resolved_at"] == T0 + 5 and done["resolution"] == "Ana means action 0"
    assert store.counter(db, "teacher_interventions") == 1
    assert executive.resolve(db, pid, "Ana means action 0", T0 + 6) == done
    assert store.counter(db, "teacher_interventions") == 1
    with pytest.raises(Conflict):
        executive.resolve(db, pid, "Ana means action 1", T0)
    with pytest.raises(NotFound):
        executive.resolve(db, 999, "x", T0)
    with pytest.raises(ValidationError):
        executive.resolve(db, pid, " ", T0)
    assert executive.pending(db) == [] and len(executive.pending(db, include_resolved=True)) == 1


def test_unknown_epoch(instance):
    db, _ = instance
    with pytest.raises(NotFound):
        executive.decide(db, "nowhere", T0)
    with pytest.raises(NotFound):
        executive.pending(db, epoch="nowhere")
