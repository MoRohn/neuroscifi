"""Arbitration in the integration loop (Wave 2 lane L, ADR-056; directive §8 "executive control must arbitrate
conflicts and permissions", "prevent any subsystem from creating uncontrolled competing objectives"; §12 "conflicting
internal priorities", "conflicting cognitive subsystem outputs").

  salience_attention (off by default)  salience orders what autonomy.admission already admitted; it admits nothing
  arbitration_record (a record)        when two mechanisms want one subject in one timer pass, the tick's existing
                                       order decides and a decision event on the winner's task names the loser
  competing objectives                 every background activity passes background.allowed; both autonomy paths
                                       admit through autonomy.admission; nothing lane L added raises its own priority,
                                       budget or frequency; quiet and a spent budget silence the whole loop
"""
import random

import pytest

from neuroscifi import background, self_description as sd
from neuroscifi.instance import store
from neuroscifi.webcortex import autonomy, curiosity, predictions, salience, scheduler, state
from neuroscifi.webcortex.config import WebConfig
from test_background import GOOD, quiet, ranked
from test_orchestrator import T0, open_individual


@pytest.fixture
def individual(tmp_path):
    yield from open_individual(tmp_path)


HOUR = 3600.0
# two knowledge items whose two orders disagree: A has the greater information gain (admission's priority), B the
# greater salience (accepted, held with little confidence)
A = (1, "ostra basin area", "The Ostra basin covers 9,400 square kilometres of lowland.", 0.5)
B = (2, "varnel river length", "The Varnel River is 412 kilometres long and drains the Ostra basin.", 0.3)
LOOP = {"autonomous": True, "idle_recheck": True, "premise_recheck": True, "salience_record": True,
        "salience_attention": True, "arbitration_record": True, "curiosity_learning_progress": True}


def stage(db, monkeypatch):
    with store.write(db):
        for (kid, key, statement, _), status, confidence in ((A, "provisional", 0.9), (B, "accepted", 0.2)):
            db.execute("INSERT INTO web_knowledge(id,key,version,statement,status,confidence,uncertainty,"
                       "freshness_class,first_verified_at,last_verified_at,created_at,updated_at) "
                       "VALUES(?,?,1,?,?,?,0.2,'stable',1,1,1,1)", (kid, key, statement, status, confidence))
    monkeypatch.setattr(curiosity, "rank", ranked(A, B))
    monkeypatch.setattr(predictions, "check_due", lambda *a, **k: [])
    autonomy.enable(db, T0)
    autonomy.set_policy(db, {"idle_interval": 0.0}, T0)


def keys(items):
    return [i["key"] for i in items]


@pytest.mark.parametrize("path", ["idle", "tick"])
def test_salience_orders_what_admission_admitted_and_admits_nothing_itself(individual, monkeypatch, path):
    db, _ = individual
    stage(db, monkeypatch)
    off = autonomy.admission(db, T0, path=path, limit=10)
    state.set_config(db, {"salience_attention": True}, T0)
    on = autonomy.admission(db, T0, path=path, limit=10)
    assert keys(off["chosen"]) == [A[1], B[1]]               # the priority order: the greater gain first
    assert keys(on["chosen"]) == [B[1], A[1]]                # the salience order: the more salient first
    assert sorted(keys(on["chosen"])) == sorted(keys(off["chosen"]))
    assert [(r["key"], r["reason"]) for r in on["refused"]] == [(r["key"], r["reason"]) for r in off["refused"]]
    assert on["order"] == {"by": "salience", "priority_rank_of_first": 1} and "order" not in off
    assert [c["priority_rank"] for c in on["chosen"]] == [1, 0]
    state.set_config(db, {"salience_record": False}, T0)     # no record to order by: the priority order stands
    assert keys(autonomy.admission(db, T0, path=path, limit=10)["chosen"]) == [A[1], B[1]]


def test_conflicting_subsystem_outputs_are_decided_once_and_the_decision_records_both(individual, monkeypatch):
    """§12 "conflicting cognitive subsystem outputs": salience wants B, curiosity's priority wants A. One rule
    decides (with salience_attention on, the salience order among the admitted), and the decision event keeps both:
    the salience record of what was taken and its place in the priority order (1: the priority order lost)."""
    db, _ = individual
    stage(db, monkeypatch)
    state.set_config(db, {"salience_attention": True}, T0)
    done = autonomy.idle(db, T0)
    assert done["action"] == "queued" and done["candidate"]["key"] == B[1]
    picked = next(e["detail"]["autonomy"] for e in state.events(db, done["task_id"]) if e["kind"] == "decision"
                  and isinstance(e["detail"].get("autonomy"), dict))
    assert picked["admission"] == {"rule": "unified", "path": "idle", "order": "salience"}
    assert picked["priority_rank"] == 1 and picked["salience"]["score"] > 0
    assert A[1] not in str(picked)            # the losing subject is named by its place, never by its words
    state.set_config(db, {"salience_attention": False}, T0)
    with store.write(db):
        db.execute("DELETE FROM web_events")                  # no cooldown from the first decision
    assert autonomy.idle(db, T0 + 1)["candidate"]["key"] == A[1]


@pytest.mark.parametrize("record", [True, False])
def test_conflicting_internal_priorities_one_task_per_subject_and_the_loser_named(individual, monkeypatch, record):
    """§12 "conflicting internal priorities": a due refresh and the timer's curiosity want one subject in one pass.
    The tick's own order decides (one task, the refresh); with arbitration_record on, the refresh's task carries a
    decision event naming curiosity as the loser and the rule; off, the same launches and no event."""
    db, _ = individual
    monkeypatch.setattr(curiosity, "rank", ranked(GOOD))
    refresh = [{"prediction_id": 1, "action": "refresh_submitted", "task_id": "refresh-task", "knowledge_id": GOOD[0]}]
    monkeypatch.setattr(predictions, "check_due", lambda *a, **k: refresh)
    autonomy.enable(db, T0)
    state.set_config(db, {"arbitration_record": record}, T0)
    report = scheduler.tick(db, T0, None, queue=True)
    assert report["queued"] == [{"task_id": "refresh-task", "origin": "refresh"}]
    events = [e["detail"]["arbitration"] for e in state.events(db, "refresh-task") if "arbitration" in e["detail"]]
    if not record:
        assert events == [] and "arbitration" not in report
        return
    assert events == [{"slot": "one investigation per subject per timer pass", "winner": "refresh",
                       "loser": "curiosity", "rule": scheduler.REFRESHED_THIS_TICK}]
    assert report["arbitration"] == [{"task_id": "refresh-task", **events[0]}]
    cycle = sd.value(sd.build(db, None, T0 + 1), "doing.cycle")
    assert cycle["arbitration"][0]["loser"] == "curiosity" and cycle["arbitration"][0]["winner"] == "refresh"


def test_the_arbitration_record_is_attached_to_the_winners_task_and_launches_nothing(individual):
    db, _ = individual
    before = db.execute("SELECT count(*) FROM web_tasks").fetchone()[0]
    report = {}
    scheduler._arbitrated(db, report, T0, "t-1", "recheck", "curiosity", scheduler.TAKEN_BY_RECHECK)
    rows = db.execute("SELECT task_id, json_extract(detail,'$.arbitration.loser') FROM web_events WHERE "
                      "kind='decision'").fetchall()
    assert rows == [("t-1", "curiosity")] and db.execute("SELECT count(*) FROM web_tasks").fetchone()[0] == before
    # it never feeds the cooldown or the lineage (they read $.autonomy.key only)
    assert autonomy._recent(db, T0 + 1, 86400.0) == set()


def _recheck_takes(db, monkeypatch, item, *, task="recheck-task"):
    """The knowledge re-check queues `item` in this pass (its decision event names the subject, so curiosity's
    cooldown refuses it afterwards), as recheck.run does."""
    from neuroscifi.webcortex import recheck

    def run(db, now, *, limit, over_cap):
        with store.write(db):
            state.log_event(db, task, "decision", {"autonomy": {"key": item[1], "admission": {"path": "recheck"}}},
                            now)
        return {"skipped": [], "submitted": [{"knowledge_id": item[0], "task_id": task}]}
    monkeypatch.setattr(recheck, "enabled", lambda db: True)
    monkeypatch.setattr(recheck, "run", run)


def _arbitrations(db):
    return [json for (json,) in db.execute("SELECT json_extract(detail,'$.arbitration.winner') FROM web_events "
                                           "WHERE kind='decision' AND json_extract(detail,'$.arbitration') IS NOT "
                                           "NULL")]


def test_a_real_conflict_between_the_recheck_and_curiosity_records_exactly_one_arbitration(individual, monkeypatch):
    """Lane Y (H1): the re-check took a subject in the timer's curiosity pool while budget was left for it."""
    db, _ = individual
    stage(db, monkeypatch)
    _recheck_takes(db, monkeypatch, A)
    report = scheduler.tick(db, T0, None, queue=True, max_tasks=3)
    assert _arbitrations(db) == ["recheck"]
    assert report["arbitration"] == [{"task_id": "recheck-task", "slot": "one investigation per subject per timer "
                                      "pass", "winner": "recheck", "loser": "curiosity",
                                      "rule": scheduler.TAKEN_BY_RECHECK}]


def test_a_subject_outside_the_timers_curiosity_pool_records_no_arbitration(individual, monkeypatch):
    """Lane Y (H1): curiosity refuses the re-checked subject by cooldown, but it was never among the timer's ten
    best (curiosity.rank limit 10), so curiosity could not have taken it: no conflict, no record."""
    db, _ = individual
    stage(db, monkeypatch)
    both, timer = ranked(B, A), ranked(B)
    monkeypatch.setattr(curiosity, "rank", lambda db, now, limit=10: (timer if limit == 10 else both)(db, now))
    _recheck_takes(db, monkeypatch, A)
    report = scheduler.tick(db, T0, None, queue=True, max_tasks=3)
    assert any(s.get("target", {}).get("key") == A[1] and str(s["reason"]).startswith("investigated autonomously")
               for s in report["skipped"])          # the cooldown refusal the old record fired on
    assert _arbitrations(db) == [] and "arbitration" not in report


def test_no_budget_left_for_curiosity_records_no_arbitration(individual, monkeypatch):
    """Lane Y (H1): the re-check spent the pass's whole budget, so curiosity could not have taken the subject."""
    db, _ = individual
    stage(db, monkeypatch)
    _recheck_takes(db, monkeypatch, A)
    report = scheduler.tick(db, T0, None, queue=True, max_tasks=1)
    assert any(s.get("target", {}).get("key") == A[1] and str(s["reason"]).startswith("investigated autonomously")
               for s in report["skipped"])
    assert _arbitrations(db) == [] and "arbitration" not in report


# ---- competing objectives (directive §8: "prevent any subsystem from creating uncontrolled competing objectives") ----

def test_every_background_activity_passes_the_one_check_with_the_whole_loop_on(individual):
    db, _ = individual
    state.set_config(db, LOOP, T0)
    store.set_config(db, {"imagine_scenarios": True}, T0)
    for name, spec in background.ACTIVITIES.items():
        assert background.allowed(db, name) == (True, None), name
    quiet(db)
    for name, spec in background.ACTIVITIES.items():
        held = spec["kind"] == "cognition"
        assert background.allowed(db, name) == ((False, background.QUIET_REASON) if held else (True, None)), name


def test_both_autonomy_paths_and_the_recheck_admit_through_the_one_rule_with_the_loop_on(individual, monkeypatch):
    db, _ = individual
    stage(db, monkeypatch)
    state.set_config(db, LOOP, T0)
    paths, real = [], autonomy.admission
    monkeypatch.setattr(autonomy, "admission", lambda *a, **k: paths.append(k["path"]) or real(*a, **k))
    scheduler.tick(db, T0, None, queue=True)
    autonomy.idle(db, T0 + 1)
    assert paths == ["recheck", "tick", "idle"]


def test_nothing_lane_l_added_raises_its_own_priority_budget_or_frequency(individual, monkeypatch):
    db, _ = individual
    stage(db, monkeypatch)
    shipped, policy = WebConfig(), autonomy.policy(db)
    off = scheduler.tick(db, T0, None, queue=True)
    state.set_config(db, {"salience_attention": True, "arbitration_record": True}, T0)
    cfg = state.get_config(db)
    assert (cfg.daily_autonomous, cfg.recheck_per_tick, cfg.recheck_daily) == \
        (shipped.daily_autonomous, shipped.recheck_per_tick, shipped.recheck_daily)
    assert autonomy.policy(db) == policy
    with store.write(db):
        db.execute("DELETE FROM web_events")
        db.execute("DELETE FROM web_tasks")
    on = scheduler.tick(db, T0, None, queue=True)
    assert len(on["queued"]) == len(off["queued"])            # as many launches; only their order may differ
    # the order is a permutation of what admission gave it, whatever the scores
    rng = random.Random(7)
    for _ in range(200):
        items = [{"key": f"k{i}", "owner": rng.random() < 0.2, "priority": rng.random(),
                  "salience": {"score": rng.random()}} for i in range(rng.randint(0, 8))]
        ordered, _ = salience.order(items)
        assert sorted(i["key"] for i in ordered) == sorted(i["key"] for i in items)
        assert [i["owner"] for i in ordered] == sorted((i["owner"] for i in items), reverse=True)
    # the self-description's new field writes nothing
    changes = db.total_changes
    sd.build(db, None, T0 + 2)
    assert db.total_changes == changes


def test_quiet_and_a_spent_budget_silence_the_whole_loop_at_the_next_pass(individual, monkeypatch):
    """Wave 0 recorded quiet honoured within one worker poll (genesis_safety.json; the running-worker test in
    test_background.py); here, with every link on, the very next pass and the next claim hold everything."""
    db, _ = individual
    stage(db, monkeypatch)
    state.set_config(db, LOOP, T0)
    store.set_config(db, {"imagine_scenarios": True}, T0)
    quiet(db)
    report = scheduler.tick(db, T0, None, queue=True)
    assert report["queued"] == [] and report.get("scenarios") == {"held": background.QUIET_REASON}
    assert autonomy.idle(db, T0 + 1)["action"] == "quiet"
    held = scheduler.claim_next(db, "w", T0 + 2)
    assert held is None
    quiet(db, False)
    state.set_config(db, {"daily_autonomous": {"tasks": 0, "requests": 0, "bytes": 0}}, T0)
    spent = scheduler.tick(db, T0 + HOUR, None, queue=True)
    assert spent["queued"] == []
    assert any("daily autonomous cap reached" in str(s.get("reason")) for s in spent["skipped"])
