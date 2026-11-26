"""Lane K (ADR-055): hypothesis fields and the 'abandoned' rule, premises and re-check marks, the idle re-check, the
reflection routine, strategy selection behind its gate and the improvement ledger. Every mechanism changes no belief:
a knowledge item moves only through consolidation."""
import json
import sqlite3

import pytest

from neuroscifi import background
from neuroscifi.instance import checkpoints, governance, imagined, schema, store
from neuroscifi.instance.errors import Conflict, ValidationError
from neuroscifi.webcortex import autonomy, curiosity, evolution, knowledge, orchestrator, predictions, premises, \
    recheck, reflection, scheduler, state, strategies
from test_consolidate import DAY, T0, consolidated, open_db, two_sources
from test_evolution import undated_latest

MIGRATION = next(i for i, script in enumerate(schema.MIGRATIONS) if "CREATE TABLE IF NOT EXISTS web_premises" in script)
OTHER = "NumPy 2.4.0 is the latest release of NumPy."


@pytest.fixture
def db(tmp_path):
    yield from open_db(tmp_path)


def knowledge_row(db, kid):
    return db.execute("SELECT status, confidence, verification, superseded_by FROM web_knowledge WHERE id=?",
                      (kid,)).fetchone()


def alternative(db, kid, now=T0):
    """A hypothesis opened against knowledge #kid (as knowledge_prediction_error does), under a key no later evidence
    in these tests settles."""
    with store.write(db):
        hid, _ = evolution.open_hypothesis(db, key="kestrel alternative", statement="Kestrel 9.9.0 is the latest.",
                                           origin="insufficient", verification={"confidence": 0.4},
                                           claims=[], values=["v9.9.0"], polarity=1, now=now, knowledge_id=kid)
    return hid


def marks(db, **where):
    sql = " AND ".join(f"{k}=?" for k in where) or "1"
    return db.execute(f"SELECT subject_kind, subject_id, reason, status FROM web_rechecks WHERE {sql} ORDER BY id",
                      tuple(where.values())).fetchall()


# ---- the migration ---------------------------------------------------------------------------------------------------

def test_the_migration_is_additive_and_refuses_an_imagined_premise_and_a_ledger_rewrite(db):
    assert MIGRATION >= 17 and "ALTER TABLE" not in schema.MIGRATIONS[MIGRATION]     # may run again on a copy
    columns = {r[1] for r in db.execute("PRAGMA table_info(web_hypothesis_details)")}
    assert {"assumptions", "counter_evidence", "expected_info_gain", "cost", "revisions"} <= columns
    path = db.execute("PRAGMA database_list").fetchone()[2]
    with sqlite3.connect(path, isolation_level=None) as raw:    # IF NOT EXISTS throughout: it can run again
        raw.executescript(schema.MIGRATIONS[MIGRATION])
    with pytest.raises(sqlite3.IntegrityError):
        with store.write(db):
            db.execute("INSERT INTO web_premises VALUES('knowledge',1,'claim',?, 'supported_by', 0)",
                       ("imagined:" + "0" * 32,))
    rid = strategies.record(db, capability="x", level=3, change="c", evidence={}, generalisation={}, regressions={},
                            cost={}, reproducibility={}, decision="revise", decided_by="gate", now=T0)
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        with store.write(db):
            db.execute("UPDATE improvement_ledger SET decision='retain' WHERE id=?", (rid,))


# ---- 1. hypothesis fields, revisions, abandonment, competition ----------------------------------------------------------

def test_a_new_hypothesis_records_assumptions_information_gain_cost_and_its_revisions(db):
    _, first = consolidated(db, [undated_latest()])
    h = evolution.hypothesis(db, first["held"][0]["hypothesis_id"])
    p = db.execute("SELECT probability FROM web_predictions WHERE hypothesis_id=?", (h["id"],)).fetchone()[0]
    assert h["assumptions"] and all(isinstance(a, str) and a for a in h["assumptions"])
    assert h["expected_info_gain"] == pytest.approx(curiosity.info_gain(p), abs=1e-6)
    assert h["cost"] == pytest.approx(curiosity.cost("fact_verification"))
    assert [r["to"] for r in h["revisions"]] == ["open"]
    tid, second = consolidated(db, [two_sources(hosts=("c.example", "d.example"))], T0 + 1)
    assert second["predictions_settled"][0]["outcome"] == "confirmed"
    revision = evolution.hypothesis(db, h["id"])["revisions"][-1]
    assert revision["to"] == "supported" and revision["evidence"]["task_id"] == tid


def test_the_fields_stay_empty_when_the_setting_is_off(db):
    state.set_config(db, {"hypothesis_fields": False}, T0)
    _, first = consolidated(db, [undated_latest()])
    h = evolution.hypothesis(db, first["held"][0]["hypothesis_id"])
    assert h["assumptions"] == [] and h["expected_info_gain"] is None and h["revisions"] == []


def test_an_alternative_to_knowledge_lists_the_items_support_as_counter_evidence(db):
    _, first = consolidated(db, [two_sources(published=T0 - 5 * DAY)])
    kid = first["provisional"][0]
    h = evolution.hypothesis(db, alternative(db, kid))
    supporting = json.loads(db.execute("SELECT supporting FROM web_knowledge WHERE id=?", (kid,)).fetchone()[0])
    assert h["counter_evidence"] == supporting and h["knowledge_id"] == kid
    assert not any("#" in a for a in h["assumptions"])     # the id is a structured field, never stored text


def test_forgetting_the_knowledge_an_alternative_was_opened_against_leaves_no_trace_of_it(db, tmp_path):
    """review1 CM2: the surviving hypothesis named the forgotten item in its assumptions and kept its support as
    counter-evidence (ids are reused: a later item could take the id)."""
    _, first = consolidated(db, [two_sources(published=T0 - 5 * DAY)])
    kid = first["provisional"][0]
    hid = alternative(db, kid)
    with store.write(db):          # a revision of it names its parent only in the structured field
        child, _ = evolution.open_hypothesis(db, key="kestrel revision", statement="Kestrel 9.9.1 is the latest.",
                                             origin="prediction_error", verification={"confidence": 0.4}, claims=[],
                                             values=["v9.9.1"], polarity=1, now=T0 + 1, parent_id=hid)
    supporting = json.loads(db.execute("SELECT supporting FROM web_knowledge WHERE id=?", (kid,)).fetchone()[0])
    path = __import__("pathlib").Path(db.execute("PRAGMA database_list").fetchone()[2])
    cp = checkpoints.create(db, path, tmp_path / "cps", T0 + 2)
    governance.forget_knowledge(db, kid, T0 + 3)
    checkpoints.restore(db, cp["id"], tmp_path / "restored.sqlite", T0 + 4)
    for conn in (db, sqlite3.connect(tmp_path / "restored.sqlite")):
        for h, text, counter in conn.execute("SELECT hypothesis_id, assumptions, counter_evidence FROM "
                                             "web_hypothesis_details WHERE hypothesis_id IN (?,?)", (hid, child)):
            assert f"#{kid}" not in text and f"#{hid}" not in text
            assert not set(json.loads(counter)) & set(supporting)
        assert conn.execute("SELECT knowledge_id FROM web_hypotheses WHERE id=?", (hid,)).fetchone() == (None,)
        assert conn.execute("SELECT parent_id FROM web_hypotheses WHERE id=?", (child,)).fetchone() == (hid,)
        if conn is not db:
            conn.close()


def test_abandoned_only_by_the_evidence_rule_and_only_when_switched_on(db):
    _, first = consolidated(db, [undated_latest()])
    hid = first["held"][0]["hypothesis_id"]
    late = T0 + evolution.MAX_HORIZON + 1
    with store.write(db):                       # off (the default): it stays open, as before
        evolution.expire(db, late)
    assert evolution.hypothesis(db, hid)["status"] == "open"
    _, other = consolidated(db, [undated_latest(text="Zlib 1.3.1 is the latest release of the zlib library.",
                                                key="zlib latest version")], T0 + 2,
                            objective="What is the latest zlib release?")
    hid2 = other["held"][0]["hypothesis_id"]
    with store.write(db):
        hid3, _ = evolution.open_hypothesis(db, key="owner subject", statement="Kestrel 8 ships a plugin API.",
                                            origin="owner", verification={}, claims=[], values=[], polarity=1,
                                            now=T0 + 2)
    state.set_config(db, {"hypothesis_abandon": True}, T0 + 3)
    with store.write(db):
        expired = evolution.expire(db, late + 10)
    assert {e["hypothesis_id"] for e in expired if e.get("abandoned")} == {hid2}
    h = evolution.hypothesis(db, hid2)
    assert h["status"] == "abandoned" and h["revisions"][-1]["to"] == "abandoned"
    assert h["revisions"][-1]["evidence"]["predictions"]
    assert evolution.hypothesis(db, hid3)["status"] == "open"      # the owner's hypothesis is never abandoned
    assert evolution.hypothesis(db, hid)["status"] == "open"       # expired before the switch: nothing re-decided


def test_a_hypothesis_with_tracked_evidence_is_not_abandoned(db):
    state.set_config(db, {"hypothesis_abandon": True}, T0)
    _, first = consolidated(db, [undated_latest()])
    hid = first["held"][0]["hypothesis_id"]
    with store.write(db):
        db.execute("UPDATE web_predictions SET detail=? WHERE hypothesis_id=?",
                   (json.dumps({"observations": [{"state": "insufficient"}]}), hid))
        evolution.expire(db, T0 + evolution.MAX_HORIZON + 1)
    assert evolution.hypothesis(db, hid)["status"] == "open"


def test_competing_hypotheses_are_compared_with_dampened_likelihoods_and_other(db):
    with store.write(db):
        for values, groups in ((["v1"], 3), (["v2"], 1)):
            evolution.open_hypothesis(db, key="k", statement=f"K is {values[0]}.", origin="conflicting",
                                      verification={"independent_groups": {"supporting": groups, "contradicting": 0}},
                                      claims=[], values=values, polarity=1, now=T0, with_prediction=False)
    out = evolution.compete(db, "k")
    posts = {m["support"]: m["posterior"] for m in out["members"] if m["hypothesis_id"] != "other"}
    other = [m for m in out["members"] if m["hypothesis_id"] == "other"][0]["posterior"]
    lr = (0.8 / 0.2) ** 0.5
    assert sum(m["posterior"] for m in out["members"]) == pytest.approx(1.0, abs=1e-5)
    assert posts[3] / posts[1] == pytest.approx(lr ** 2, rel=1e-4)          # undampened would be 16
    assert posts[1] / other == pytest.approx(lr, rel=1e-4) and posts[3] / posts[1] < 16


# ---- 2 and 4. premises, re-check marks, failed predictions -----------------------------------------------------------------

def test_new_knowledge_and_hypotheses_record_what_they_rest_on(db):
    _, first = consolidated(db, [two_sources(published=T0 - 5 * DAY)])
    kid = first["provisional"][0]
    supporting = json.loads(db.execute("SELECT supporting FROM web_knowledge WHERE id=?", (kid,)).fetchone()[0])
    assert {(p["kind"], p["id"]) for p in premises.premises_of(db, "knowledge", kid)} == \
        {("claim", c) for c in supporting}
    hid = alternative(db, kid)
    assert premises.premises_of(db, "hypothesis", hid) == [{"kind": "knowledge", "id": str(kid),
                                                            "relation": "alternative_to"}]


def test_a_superseded_premise_leaves_no_mark_nothing_could_re_verify_and_changes_nothing(db):
    """review1 CM3: a hypothesis is re-checked by re-verifying the live knowledge it rests on; once that item is
    superseded nothing could, so no mark is made (it never closed before)."""
    _, first = consolidated(db, [two_sources(published=T0 - 5 * DAY)])
    kid = first["provisional"][0]
    hid = alternative(db, kid)
    before = evolution.hypothesis(db, hid)
    _, second = consolidated(db, [two_sources(published=T0 - DAY, text=OTHER, hosts=("c.example", "d.example"))],
                             T0 + DAY)
    assert second["superseded"][0]["old"] == kid
    assert marks(db) == [] and not (second.get("rechecks") or {}).get("marked")
    after = evolution.hypothesis(db, hid)
    assert {k: after[k] for k in ("status", "verification", "claims", "statement")} == \
        {k: before[k] for k in ("status", "verification", "claims", "statement")}


def test_without_the_setting_no_premise_is_recorded_and_nothing_is_marked(db):
    state.set_config(db, {"premise_recheck": False}, T0)
    _, first = consolidated(db, [two_sources(published=T0 - 5 * DAY)])
    alternative(db, first["provisional"][0])
    consolidated(db, [two_sources(published=T0 - DAY, text=OTHER, hosts=("c.example", "d.example"))], T0 + DAY)
    assert db.execute("SELECT count(*) FROM web_premises").fetchone()[0] == 0 and marks(db) == []


def test_a_failed_prediction_marks_what_it_rested_on_and_no_belief_moves(tmp_path):
    rows = {}
    for setting in (True, False):
        (tmp_path / str(setting)).mkdir()
        opened = open_db(tmp_path / str(setting))
        conn = next(opened)
        try:
            state.set_config(conn, {"premise_recheck": setting}, T0)
            _, first = consolidated(conn, [two_sources(published=T0 - 5 * DAY)])
            kid = first["provisional"][0]
            hid = alternative(conn, kid)
            pid = predictions.record(conn, kid, "still holds at next refresh", T0 + DAY, T0)["id"]
            with store.write(conn):          # a contradiction the refresh found (not a supersession)
                conn.execute("UPDATE web_knowledge SET contradicting=? WHERE id=?",
                             (json.dumps([{"claim": "c1", "text": OTHER}]), kid))
            settled = predictions.check(conn, pid, {"consolidation": {"contested": [{"knowledge_id": kid,
                                                                                     "reason": "differs"}]}},
                                        T0 + DAY)
            assert settled["outcome"] == "error"
            rows[setting] = (knowledge_row(conn, kid), marks(conn, reason="prediction_failed"), kid, hid)
        finally:
            opened.close()
    on, off = rows[True], rows[False]
    assert on[0] == off[0]                                      # the item is exactly as without the mechanism
    assert ("knowledge", on[2], "prediction_failed", "marked") in on[1]
    assert ("hypothesis", on[3], "prediction_failed", "marked") in on[1] and off[1] == []


def test_forgetting_a_premise_marks_its_dependents_and_removes_every_reference_also_on_restore(db, tmp_path):
    _, first = consolidated(db, [two_sources(published=T0 - 5 * DAY)])
    kid = first["provisional"][0]
    _, other = consolidated(db, [undated_latest(text="Zlib 1.3.1 is the latest release of the zlib library.",
                                                key="zlib latest version")], T0 + 1,
                            objective="What is the latest zlib release?")
    hid = alternative(db, kid, T0 + 2)
    with store.write(db):
        premises.mark(db, "knowledge", kid, "idle", {"kind": "knowledge", "id": str(kid)}, T0 + 2)
    path = __import__("pathlib").Path(db.execute("PRAGMA database_list").fetchone()[2])
    cp = checkpoints.create(db, path, tmp_path / "cps", T0 + 3)
    out = governance.forget_knowledge(db, kid, T0 + 4)
    assert out["effects"]["premises"]["marked"] == 0        # hid rested only on kid: nothing left to re-verify
    for conn in (db, None):
        if conn is None:
            checkpoints.restore(db, cp["id"], tmp_path / "restored.sqlite", T0 + 5)
            conn = sqlite3.connect(tmp_path / "restored.sqlite")
        assert conn.execute("SELECT count(*) FROM web_premises WHERE premise_kind='knowledge' AND premise_id=?",
                            (str(kid),)).fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM web_rechecks WHERE subject_kind='knowledge' AND subject_id=?",
                            (kid,)).fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM web_rechecks WHERE cause LIKE ?", (f'%"{kid}"%',)).fetchone()[0] == 0
        if conn is db:
            assert marks(db, subject_kind="hypothesis") == [] and hid
        else:
            conn.close()


# ---- marks close (review1 CM3, CL6) ------------------------------------------------------------------------------------------

def _contradicted(db, now=T0 + 1):
    """Knowledge #kid, a hypothesis opened against it, and an older conflicting source that contradicts it."""
    _, first = consolidated(db, [two_sources()])
    kid = first["provisional"][0]
    hid = alternative(db, kid)
    _, out = consolidated(db, [two_sources(published=T0 - 3 * DAY, text="NumPy 2.2.0 is the latest NumPy release.",
                                           hosts=("old1.example", "old2.example"))], now)
    assert out["contested"][0]["knowledge_id"] == kid
    return kid, hid


def test_a_mark_closes_when_any_normal_path_re_verifies_its_subject_with_the_re_check_off(db):
    kid, hid = _contradicted(db)
    assert recheck.enabled(db) is False
    assert marks(db) == [("hypothesis", hid, "premise_contradicted", "marked")]
    assert recheck.rank(db, T0 + 2)[0]["why"] == "marked to re-check"      # its path: re-verify the premise
    with store.write(db):
        premises.mark(db, "knowledge", kid, "reflection", {}, T0 + 2)
    consolidated(db, [two_sources(hosts=("c.example", "d.example"))], T0 + DAY)    # an owner's task confirms it
    rows = db.execute("SELECT subject_kind, status, outcome FROM web_rechecks ORDER BY id").fetchall()
    assert rows == [("hypothesis", "done", "closed: its knowledge premise was re-verified since it was marked"),
                    ("knowledge", "done", "closed: re-verified since it was marked")]


def test_a_mark_closes_when_its_subject_is_superseded_or_settled(db):
    kid, hid = _contradicted(db)
    with store.write(db):
        premises.mark(db, "knowledge", kid, "reflection", {}, T0 + 2)
    consolidated(db, [two_sources(published=T0 + DAY, text=OTHER, hosts=("c.example", "d.example"))], T0 + 2 * DAY)
    assert knowledge.row(db, kid)["status"] == "superseded"
    assert dict(db.execute("SELECT subject_kind, outcome FROM web_rechecks").fetchall()) == {
        "knowledge": "closed: the item is superseded",
        "hypothesis": "closed: no live knowledge premise is left to re-verify"}


def test_a_hypothesis_resting_on_no_live_knowledge_is_not_marked(db):
    _, first = consolidated(db, [undated_latest()])
    hid = first["held"][0]["hypothesis_id"]
    with store.write(db):
        assert premises.mark(db, "hypothesis", hid, "reflection", {}, T0 + 1) is None
    assert marks(db) == []


def test_a_mark_whose_re_verification_keeps_failing_is_dropped(db):
    """review1 CL6: release_stuck put it back to 'marked' for ever."""
    _, first = consolidated(db, [two_sources()])
    kid = first["provisional"][0]
    with store.write(db):
        mid = premises.mark(db, "knowledge", kid, "reflection", {}, T0 + 1)
    for attempt in range(1, premises.MAX_ATTEMPTS + 1):
        task = orchestrator.submit(db, f"What is the latest numpy release? (try {attempt})", T0 + attempt,
                                   mode="quick_lookup", origin="console")["id"]
        with store.write(db):
            db.execute("UPDATE web_rechecks SET status='queued', task_id=? WHERE id=?", (task, mid))
            db.execute("UPDATE web_tasks SET status='failed' WHERE id=?", (task,))
            premises.release_stuck(db, T0 + attempt)
    status, outcome, cause = db.execute("SELECT status, outcome, cause FROM web_rechecks WHERE id=?",
                                        (mid,)).fetchone()
    assert status == "dropped" and "3 times" in outcome and json.loads(cause)["attempts"] == premises.MAX_ATTEMPTS


def test_marks_do_not_accumulate_over_a_long_run(db):
    """review1 CM3: sixty days, each a new item contradicted (its hypothesis marked) and re-verified the next day by
    an ordinary refresh, the idle re-check off throughout. Open marks stay bounded; before, every one stayed open."""
    open_counts = []
    for d in range(60):
        name = f"Pkg{d}"
        text = f"{name} 1.{d}.0 is the latest release of the {name.lower()} library."
        key = f"{name.lower()} latest version"
        at = T0 + d * DAY
        _, first = consolidated(db, [two_sources(text=text, key=key, published=at - DAY)], at,
                                objective=f"What is the latest {name} release?")
        kid = first["provisional"][0]
        with store.write(db):
            evolution.open_hypothesis(db, key=f"alternative {d}", statement=f"{name} 9.9.0 is the latest.",
                                      origin="insufficient", verification={"confidence": 0.4}, claims=[],
                                      values=["v9.9.0"], polarity=1, now=at, knowledge_id=kid)
            premises.premise_changed(db, "knowledge", kid, "premise_contradicted", at)
        if d:
            prev = f"Pkg{d - 1}"
            consolidated(db, [two_sources(text=f"{prev} 1.{d - 1}.0 is the latest release of the {prev.lower()} "
                                               "library.", key=f"{prev.lower()} latest version", published=at - DAY,
                                          hosts=(f"r{d}a.example", f"r{d}b.example"))], at + 3600,
                         objective=f"What is the latest {prev} release?")
        open_counts.append(db.execute("SELECT count(*) FROM web_rechecks WHERE status IN ('marked','queued')"
                                      ).fetchone()[0])
    assert db.execute("SELECT count(*) FROM web_rechecks").fetchone()[0] == 60
    assert max(open_counts) <= 2 and open_counts[-1] == 1


def test_a_concurrent_re_check_pass_queues_no_second_task(db):
    """review1 CL5/CL7: the task, the decision event, the prediction link and the mark are one transaction, and a
    second pass for the same item writes nothing."""
    a, _ = _two_items(db)
    state.set_config(db, {"idle_recheck": True}, T0)
    autonomy.enable(db, T0)
    pick = next(p for p in recheck.rank(db, T0) if p["knowledge_id"] == a)
    admitted = {"rule": "unified", "path": "recheck"}
    task = recheck.submit(db, pick, admitted, T0)
    tasks = db.execute("SELECT count(*) FROM web_tasks").fetchone()[0]
    events = db.execute("SELECT count(*) FROM web_events WHERE kind='decision'").fetchone()[0]
    with pytest.raises(Conflict, match="already queued"):
        recheck.submit(db, pick, admitted, T0 + 1)
    assert db.execute("SELECT count(*) FROM web_tasks").fetchone()[0] == tasks
    assert db.execute("SELECT count(*) FROM web_events WHERE kind='decision'").fetchone()[0] == events
    assert marks(db, status="queued") == [("knowledge", a, "idle", "queued")] and task["origin"] == f"refresh:{a}"


# ---- 3. the idle re-check --------------------------------------------------------------------------------------------------

def _two_items(db):
    _, a = consolidated(db, [two_sources(published=T0 - 61 * DAY)], T0 - 60 * DAY)
    _, b = consolidated(db, [two_sources(published=T0 - 61 * DAY, key="zlib latest version",
                                         text="Zlib 1.3.1 is the latest release of the zlib library.")],
                        T0 - 60 * DAY, objective="What is the latest zlib release?")
    with store.write(db):       # the staging tasks are finished research
        db.execute("UPDATE web_tasks SET status='completed'")
    return a["provisional"][0], b["provisional"][0]


def test_the_re_check_ranks_by_importance_staleness_and_dependents(db, monkeypatch):
    a, b = _two_items(db)
    ranked = {c["knowledge_id"]: c for c in recheck.rank(db, T0)}
    assert set(ranked) == {a, b} and ranked[a]["staleness"] == pytest.approx(60 / 7, abs=1e-4)
    assert ranked[a]["value"] == pytest.approx(0.7 * 60 / 7, abs=1e-4) and recheck.DEPENDENT_WEIGHT == 0.0
    alternative(db, b)                                      # b now has a dependent: shown, weighted 0 by default
    assert {c["knowledge_id"]: c for c in recheck.rank(db, T0)}[b]["dependents"] == 1
    assert recheck.rank(db, T0)[0]["knowledge_id"] == a     # equal value: the lower id first
    monkeypatch.setattr(recheck, "DEPENDENT_WEIGHT", 1.0)
    assert recheck.rank(db, T0)[0]["knowledge_id"] == b
    monkeypatch.setattr(recheck, "DEPENDENT_WEIGHT", 0.0)
    knowledge.review(db, a, True, T0)                       # the owner accepts a: importance 1.0 against 0.7
    ranked = {c["knowledge_id"]: c for c in recheck.rank(db, T0)}
    assert ranked[a]["importance"] == 1.0 and ranked[a]["value"] > ranked[b]["value"]
    with store.write(db):
        premises.mark(db, "knowledge", b, "prediction_failed", {}, T0)
    ranked = {c["knowledge_id"]: c for c in recheck.rank(db, T0)}
    assert ranked[b]["p_current"] == 0.5 and ranked[b]["staleness"] == pytest.approx(60 / 7 + 1, abs=1e-4)


def test_the_re_check_is_admitted_by_the_one_rule_bounded_and_closed_by_the_evidence(db):
    a, b = _two_items(db)
    state.set_config(db, {"idle_recheck": True, "autonomous": True}, T0)
    assert recheck.run(db, T0)["submitted"] == []           # no recorded owner enable: the rule's gate
    autonomy.enable(db, T0)
    pid = predictions.record(db, a, "still holds at next refresh", T0 - DAY, T0 - 60 * DAY)["id"]
    out = recheck.run(db, T0)
    assert len(out["submitted"]) == 1                       # recheck_per_tick = 1
    task_id, kid = out["submitted"][0]["task_id"], out["submitted"][0]["knowledge_id"]
    task = state.task(db, task_id)
    assert task["origin"] == f"refresh:{kid}" and task["status"] == "queued"
    decision = [e["detail"] for e in state.events(db, task_id) if e["kind"] == "decision"][0]["autonomy"]
    assert decision["admission"] == {"rule": "unified", "path": "recheck"} and decision["key"]
    assert marks(db, status="queued") == [("knowledge", kid, "idle", "queued")]
    if kid == a:
        assert evolution.prediction(db, pid)["task_id"] == task_id     # check_due settles it from this task
    second = recheck.run(db, T0 + 60)
    assert all(s["knowledge_id"] != kid for s in second["submitted"])   # queued: not chosen again
    # The evidence closes the mark: the refresh's consolidation confirms the item.
    with store.write(db):
        state.save_clusters(db, task_id, [two_sources(published=T0 - DAY, hosts=("c.example", "d.example"))
                                          if kid == a else
                                          two_sources(published=T0 - DAY, key="zlib latest version",
                                                      text="Zlib 1.3.1 is the latest release of the zlib library.",
                                                      hosts=("c.example", "d.example"))], T0 + 1)
    from neuroscifi.webcortex import consolidate
    consolidate.run(db, task_id, T0 + 1)
    assert db.execute("SELECT status, outcome FROM web_rechecks WHERE task_id=?", (task_id,)).fetchone() == \
        ("done", "confirmed by new evidence")


def test_the_re_check_keeps_its_daily_share_and_never_runs_while_off(db):
    _two_items(db)
    autonomy.enable(db, T0)
    assert recheck.enabled(db) is False and recheck.run(db, T0)["submitted"] == []    # off by default
    state.set_config(db, {"idle_recheck": True, "recheck_daily": 1, "recheck_per_tick": 5}, T0)
    assert len(recheck.run(db, T0)["submitted"]) == 1
    assert recheck.run(db, T0 + 3600)["submitted"] == []   # the day's share is spent


def test_the_re_check_does_not_starve_the_owners_work(db):
    _two_items(db)
    autonomy.enable(db, T0)
    state.set_config(db, {"idle_recheck": True}, T0)
    rechecking = recheck.run(db, T0)["submitted"][0]["task_id"]
    owner = orchestrator.submit(db, "What is the latest sqlite release?", T0 + 60, mode="quick_lookup",
                                origin="console")["id"]
    claimed = scheduler.claim_next(db, "w", T0 + 61)
    assert claimed[0] == owner != rechecking


# ---- 5. reflection ---------------------------------------------------------------------------------------------------------

def _history(db):
    _, first = consolidated(db, [two_sources(published=T0 - 5 * DAY)])
    kid = first["provisional"][0]
    pid = predictions.record(db, kid, "still holds at next refresh", T0 + DAY, T0)["id"]
    _, second = consolidated(db, [two_sources(published=T0 - DAY, text=OTHER, hosts=("c.example", "d.example"))],
                             T0 + DAY)
    predictions.check(db, pid, {"consolidation": second, "task_id": second["task_id"]}, T0 + DAY)
    consolidated(db, [undated_latest(text="Zlib 1.3.1 is the latest release of the zlib library.",
                                     key="zlib latest version")], T0 + DAY + 1,
                 objective="What is the latest zlib release?")
    return kid, pid, second


def test_reflection_is_deterministic_traceable_and_bounded(db):
    kid, pid, second = _history(db)
    one = reflection.report(db, T0 - 1, T0 + 2 * DAY)
    assert one == reflection.report(db, T0 - 1, T0 + 2 * DAY)
    for section in reflection.SECTIONS:
        for line in one[section]:
            assert isinstance(line["refs"], list)
    assert any(line["kind"] == "prediction_failed" and ["prediction", str(pid)] in line["refs"]
               for line in one["failed"])
    assert any(["knowledge", str(second["superseded"][0]["new"])] in line["refs"] for line in one["learned"])
    assert "OTHER" not in json.dumps(one) and "2.4.0" not in json.dumps(one)       # no statement is quoted
    stored = reflection.reflect(db, T0 + 2 * DAY, since=T0 - 1)
    assert len(stored["actions"]) <= reflection.MAX_ACTIONS
    assert stored["report"] == one | {"period": {"from": T0 - 1.0, "to": T0 + 2 * DAY}}


def test_reflection_actions_queue_re_checks_and_never_change_knowledge(db):
    _, first = consolidated(db, [two_sources(published=T0 - 5 * DAY)])
    kid = first["provisional"][0]
    pid = predictions.record(db, kid, "still holds at next refresh", T0 + DAY, T0)["id"]
    with store.write(db):       # a failed prediction about an item that is still live
        db.execute("UPDATE web_predictions SET outcome='error', checked_at=? WHERE id=?", (T0 + DAY, pid))
    before = knowledge_row(db, kid)
    out = reflection.reflect(db, T0 + 2 * DAY, since=T0)
    assert [a["action"] for a in out["actions"]] == ["recheck"]
    assert marks(db) == [("knowledge", kid, "reflection", "marked")] and knowledge_row(db, kid) == before


def test_an_optional_model_summary_is_an_inference_in_the_imagined_store_never_knowledge(db):
    _history(db)

    class Gateway:
        def guard(self):
            return "local"

        def chat(self, messages, **kw):
            return type("Reply", (), {"text": "One item was superseded.", "model": "fake"})()

    knowledge_before = db.execute("SELECT count(*) FROM web_knowledge").fetchone()[0]
    out = reflection.reflect(db, T0 + 2 * DAY, since=T0 - 1, phrase=True, gateway=Gateway())
    record = imagined.show(db, out["summary"]["imagined_id"])
    assert record["kind"] == "inference" and out["summary"]["generated_by"] == "model:fake"
    assert db.execute("SELECT count(*) FROM web_knowledge").fetchone()[0] == knowledge_before
    assert "summary" not in reflection.show(db, out["id"])                  # the stored report has no model text


def test_a_model_summary_goes_when_a_record_it_phrases_is_forgotten(db):
    """review1 CL1: it was stored with no lineage and outlived the forget of what it phrased."""
    kid, _, _ = _history(db)

    class Gateway:
        def guard(self):
            return "local"

        def chat(self, messages, **kw):
            return type("Reply", (), {"text": "One item was superseded.", "model": "fake"})()

    out = reflection.reflect(db, T0 + 2 * DAY, since=T0 - 1, phrase=True, gateway=Gateway())
    rid = out["summary"]["imagined_id"]
    assert ["knowledge", str(kid)] in imagined.show(db, rid)["derived_from"]
    governance.forget_knowledge(db, kid, T0 + 3 * DAY)
    assert db.execute("SELECT 1 FROM imagined WHERE id=?", (rid,)).fetchone() is None


def test_a_scheduled_reflection_while_writes_are_paused_skips_and_the_tick_goes_on(db, monkeypatch):
    """review1 CL2: WritesPaused aborted the whole timer tick on every pass."""
    from neuroscifi.instance.errors import WritesPaused

    def paused(_db):
        raise WritesPaused("writes are paused: the journal is full")
    state.set_config(db, {"reflection": True}, T0)
    monkeypatch.setattr(state, "require_writable", paused)
    out = reflection.scheduled(db, T0 + DAY)
    assert "writes are paused" in out["skipped"]


def test_forgetting_drops_the_reflection_lines_that_name_it(db):
    kid, pid, second = _history(db)
    rid = reflection.reflect(db, T0 + 2 * DAY, since=T0 - 1)["id"]
    governance.forget_research(db, second["task_id"], T0 + 3 * DAY)
    text = json.dumps(reflection.show(db, rid))
    assert f'"{second["task_id"]}"' not in text


# ---- 6 and 7. strategy selection, its gate, the ledger --------------------------------------------------------------------------

def _finished(db, mode, n, *, verified):
    for i in range(n):
        prepared = orchestrator.prepare(db, f"Question {mode} {i} about ostra?", T0 + i, mode=mode, origin="test")
        with store.write(db):
            tid = orchestrator.insert(db, prepared, T0 + i)
            db.execute("UPDATE web_tasks SET status='completed', finished_at=? WHERE id=?", (T0 + i, tid))
            if verified:
                db.execute("INSERT INTO web_knowledge(key,version,statement,status,confidence,uncertainty,"
                           "freshness_class,first_verified_at,last_verified_at,valid_from,supporting,contradicting,"
                           "sources,claim_values,polarity,task_id,created_at,updated_at,verification) VALUES(?,1,?,"
                           "'provisional',0.8,0.1,'stable',?,?,?,'[]','[]','[]','[]',1,?,?,?,?)",
                           (f"k {mode} {i}", f"S {i}.", T0, T0, T0, tid, T0, T0, json.dumps({"state": "supported"})))


def test_the_reward_is_an_external_outcome_and_self_reported_success_earns_nothing(db):
    _finished(db, "quick_lookup", 6, verified=True)
    _finished(db, "continuous", 6, verified=False)
    with store.write(db):           # the inflating mode records success for itself
        db.execute("INSERT INTO web_strategies(name,state,updated_at) VALUES('mode:continuous',?,?)",
                   (json.dumps({"runs": 500, "completed": 500, "success": 500}), T0))
    out = strategies.outcomes(db)
    assert out["quick_lookup"]["rewarded"] == 6 and out["continuous"]["rewarded"] == 0
    assert strategies.posterior(db)["continuous"] == (1, 7)
    ranked, info = strategies.rank(db, T0, [{"key": "a", "mode": "continuous", "priority": 1.0},
                                            {"key": "b", "mode": "quick_lookup", "priority": 1.0}], seed=1)
    assert ranked[0]["key"] == "b" and info["rule"] == "thompson"


GOOD = {"held_out": {"run_id": "h", "strategy": {"mechanism": {"verified_per_investigation": 0.40,
                                                               "inflator_share": 0.0},
                                                 "incumbent": {"verified_per_investigation": 0.30,
                                                               "inflator_share": 0.1}}},
        "retention": {"run_id": "r", "all_scenarios_pass": True, "all_retained": True},
        "withheld": {"ledger_line": 2, "run_id": "w", "not_worse": True}}


@pytest.fixture
def final_ledger(tmp_path):
    """A final-runs ledger whose line 2 is the withheld run GOOD names (run id "w"): a run of strategy selection
    without ablations that the main session recorded as not worse (review1b N1)."""
    path = tmp_path / "genesis_final_uses.jsonl"
    path.write_text(json.dumps({"at": "b", "families": ["withheld"], "mechanism": "baseline", "ablate": []}) + "\n"
                    + json.dumps({"at": "w", "families": ["withheld"], "mechanism": strategies.FINAL_MECHANISM,
                                  "ablate": [], "not_worse": True}) + "\n")
    return path


def test_the_gate_refuses_a_final_run_that_never_evaluated_strategy_selection(db, final_ledger):
    """review1b N1: the gate accepted any withheld line (the repository's only one is the baseline's) and took "not
    worse" from the evidence file. Now the named line must be a run of strategy selection without ablations that
    itself records "not worse"."""
    from pathlib import Path
    repository = Path(strategies.__file__).resolve().parents[3] / "docs/quality/evidence/genesis_final_uses.jsonl"
    first = json.loads(repository.read_text().splitlines()[0])
    today = GOOD | {"withheld": {"ledger_line": 1, "run_id": first["at"], "not_worse": True}}
    check = strategies.gate(today)["checks"]["withheld_by_main_session"]          # the repository's ledger as it is
    if not any(json.loads(t).get("mechanism") == strategies.FINAL_MECHANISM for t in repository.read_text().splitlines()
               if t.strip()):
        assert check["pass"] is False and "no withheld run evaluated strategy selection" in check["detail"]
    lines = [{"at": "b", "families": ["withheld"], "mechanism": "baseline", "ablate": []},
             {"at": "w", "families": ["withheld"], "mechanism": strategies.FINAL_MECHANISM, "ablate": [],
              "not_worse": True}]
    for change, why in (({"ablate": ["thompson"]}, "no withheld run evaluated"),     # an ablation is not the mechanism
                        ({"not_worse": None}, "does not record the run as not worse"),
                        ({"not_worse": False}, "does not record the run as not worse")):
        final_ledger.write_text("\n".join(json.dumps(e | (change if e["at"] == "w" else {})) for e in lines))
        check = strategies.gate(GOOD, ledger=final_ledger)["checks"]["withheld_by_main_session"]
        assert check["pass"] is False and why in check["detail"], (change, check)
    final_ledger.write_text("\n".join(json.dumps(e) for e in lines))
    baseline = GOOD | {"withheld": {"ledger_line": 1, "run_id": "b", "not_worse": True}}   # the baseline's line
    check = strategies.gate(baseline, ledger=final_ledger)["checks"]["withheld_by_main_session"]
    assert check["pass"] is False and "not a run of the strategy_selection mechanism" in check["detail"]
    assert strategies.gate(GOOD, ledger=final_ledger)["passed"] is True
    silent = [lines[0], {k: v for k, v in lines[1].items() if k != "ablate"}]      # review1c: says nothing of ablations
    final_ledger.write_text("\n".join(json.dumps(e) for e in silent))
    check = strategies.gate(GOOD, ledger=final_ledger)["checks"]["withheld_by_main_session"]
    assert check["pass"] is False and "no withheld run evaluated" in check["detail"]


def test_an_installation_without_the_ledger_records_no_path(db, tmp_path, monkeypatch):
    """review1b N2: installed without the docs folder, the gate's reason named the absolute install path, and that
    reason went into the permanent improvement ledger."""
    fake = tmp_path / "site" / "pkgs" / "neuroscifi" / "webcortex" / "strategies.py"
    monkeypatch.setattr(strategies, "__file__", str(fake))
    verdict = strategies.promote(db, GOOD, T0)
    check = verdict["checks"]["withheld_by_main_session"]
    assert check["pass"] is False and check["detail"] == "the final-runs ledger is not available in this installation"
    stored = json.dumps(strategies.ledger(db, capability=strategies.CAPABILITY))
    assert str(tmp_path) not in stored and "/" not in stored.replace("\\/", "")


def test_the_permanent_ledger_keeps_no_free_text(db, final_ledger):
    """review1b N3: the owner's free-text reason and the evidence file's strings went into a ledger that no forget
    reaches. A revert takes a reason code; of the evidence only numbers, booleans and run ids are kept."""
    with pytest.raises(ValidationError, match="reason must be one of"):
        strategies.revert(db, T0, reason="my neighbour Alice said it was bad")
    strategies.revert(db, T0, reason="worse_results")
    strategies.revert(db, T0 + 1)
    secret = "Alice Example, 12 Some Street"
    evidence = json.loads(json.dumps(GOOD))
    evidence["held_out"]["run_id"] = secret
    evidence["retention"]["all_retained"] = secret
    evidence["cost"] = {"seconds": 12.5, "note": secret, secret: 1, "calls": [3, secret], "nested": {"a": {"b": {}}}}
    evidence["reproducibility"] = {"commit": "19f80e2", "command": secret}
    strategies.promote(db, evidence, T0 + 2, ledger=final_ledger)
    rows = strategies.ledger(db, capability=strategies.CAPABILITY)
    assert [r["change"] for r in rows[1:]] == ["reverted by the owner", "reverted by the owner (worse_results)"]
    assert secret not in json.dumps(rows) and "Alice" not in json.dumps(rows)
    assert rows[0]["cost"] == {"seconds": 12.5, "note": None, "calls": [3, None], "nested": {"a": {"b": None}}}
    assert rows[0]["reproducibility"] == {"commit": "19f80e2", "command": None}
    assert rows[0]["evidence"]["runs"]["held_out"] is None and rows[0]["evidence"]["runs"]["withheld"] == "w"


def test_the_cli_takes_a_reason_code_only(db, capsys):
    from neuroscifi.cli import main
    path = db.execute("PRAGMA database_list").fetchone()[2]
    with pytest.raises(SystemExit):
        main(["knowledge", "strategy", "--db", path, "--revert", "--reason", "free text about a person"])
    assert "invalid choice" in capsys.readouterr().err
    assert main(["knowledge", "strategy", "--db", path, "--revert", "--reason", "testing"]) == 0
    assert strategies.ledger(db, capability=strategies.CAPABILITY)[0]["change"] == "reverted by the owner (testing)"


def test_the_gate_checks_the_named_final_run_in_the_ledger(db, final_ledger):
    """review1 CL4 / security S7: any line number and run id passed."""
    assert strategies.gate(GOOD, ledger=final_ledger)["passed"] is True
    for withheld, why in (({"ledger_line": 3, "run_id": "w"}, "2 line(s)"),
                          ({"ledger_line": 1, "run_id": "w"}, "run id w"),
                          ({"ledger_line": 0, "run_id": "w"}, "names no ledger line")):
        evidence = GOOD | {"withheld": withheld | {"not_worse": True}}
        check = strategies.gate(evidence, ledger=final_ledger)["checks"]["withheld_by_main_session"]
        assert check["pass"] is False and why in check["detail"]
    final_ledger.write_text(json.dumps({"at": "b"}) + "\n" + json.dumps({"at": "w", "families": ["info_value"]}))
    assert strategies.gate(GOOD, ledger=final_ledger)["passed"] is False         # not a withheld run
    assert strategies.gate(GOOD, ledger=final_ledger.with_name("missing.jsonl"))["passed"] is False


def test_the_ledger_refuses_a_delete_and_an_older_file_gets_the_trigger(db):
    """Security S7: deleting the owner's revert row switched strategy selection back on. Migration 18 is unreleased
    and amended; a file migrated by its first build (no delete trigger) gets it at its next open."""
    strategies.revert(db, T0, reason="not_wanted")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        with store.write(db):
            db.execute("DELETE FROM improvement_ledger")
    db.execute("DROP TRIGGER improvement_ledger_no_delete")           # the first build of 18
    assert schema.ensure_triggers(db) == ["improvement_ledger_no_delete"]
    assert schema.ensure_triggers(db) == []
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        with store.write(db):
            db.execute("DELETE FROM improvement_ledger")


def test_a_restore_keeps_the_owners_revert_in_force(db, tmp_path, final_ledger):
    """review1 CL3: restoring an archive taken before the owner's revert switched strategy selection back on."""
    strategies.promote(db, GOOD, T0, ledger=final_ledger)
    assert strategies.active(db) is True
    path = __import__("pathlib").Path(db.execute("PRAGMA database_list").fetchone()[2])
    cp = checkpoints.create(db, path, tmp_path / "cps", T0 + 1)
    strategies.revert(db, T0 + 2, reason="not_wanted")
    out = checkpoints.restore(db, cp["id"], tmp_path / "restored.sqlite", T0 + 3)
    assert out["settings_carried"][f"improvement_ledger:{strategies.CAPABILITY}"] == {"from": "retain",
                                                                                      "to": "revert"}
    copy = store.open_existing(tmp_path / "restored.sqlite")
    try:
        assert strategies.active(copy) is False
    finally:
        copy.close()
    later = checkpoints.create(db, path, tmp_path / "cps", T0 + 4)       # an archive that already holds it
    again = checkpoints.restore(db, later["id"], tmp_path / "again.sqlite", T0 + 5)
    assert f"improvement_ledger:{strategies.CAPABILITY}" not in again["settings_carried"]


def test_strategy_selection_is_off_until_its_gate_passes_and_the_owner_can_revert_it(db, final_ledger):
    assert strategies.active(db) is False
    chosen = [{"key": "a", "mode": "quick_lookup", "priority": 1.0}, {"key": "b", "mode": "continuous",
                                                                       "priority": 2.0}]
    for missing in ("held_out", "retention", "withheld"):
        verdict = strategies.promote(db, {k: v for k, v in GOOD.items() if k != missing}, T0, ledger=final_ledger)
        assert verdict["passed"] is False and strategies.active(db) is False
    assert strategies.promote(db, GOOD, T0, ledger=final_ledger)["passed"] is True and strategies.active(db) is True
    state.set_config(db, {"strategy_selection": "off"}, T0)
    assert strategies.active(db) is False
    state.set_config(db, {"strategy_selection": "gate"}, T0)
    strategies.revert(db, T0 + 1, reason="not_wanted")
    assert strategies.active(db) is False
    rows = strategies.ledger(db, capability=strategies.CAPABILITY)
    assert [r["decided_by"] for r in rows][:2] == ["owner", "gate"] and rows[0]["decision"] == "revert"
    assert chosen      # the ranking itself is tested above; admission only calls it while active


def test_reward_hacking_is_never_promoted(db, final_ledger):
    hacked = json.loads(json.dumps(GOOD))
    hacked["held_out"]["strategy"]["mechanism"]["inflator_share"] = 0.5      # more on the self-reporting arm
    verdict = strategies.promote(db, hacked, T0, ledger=final_ledger)
    assert verdict["checks"]["reward_hacking"]["pass"] is False and strategies.active(db) is False
    small = json.loads(json.dumps(GOOD))
    small["held_out"]["strategy"]["mechanism"]["verified_per_investigation"] = 0.31   # below the minimum effect
    assert strategies.promote(db, small, T0, ledger=final_ledger)["passed"] is False


def test_levels_four_and_five_are_never_recorded_as_adopted(db):
    for level in (4, 5):
        with pytest.raises(Conflict, match="authorisation"):
            strategies.record(db, capability="weights", level=level, change="fine-tune", evidence={},
                              generalisation={}, regressions={}, cost={}, reproducibility={}, decision="retain",
                              decided_by="owner", now=T0)


def test_admission_orders_by_strategy_only_while_active(db, monkeypatch):
    calls = []
    monkeypatch.setattr(strategies, "rank", lambda db, now, chosen: (calls.append(1) or list(reversed(chosen)),
                                                                      {"rule": "thompson"}))
    autonomy.admission(db, T0, path="idle")
    assert calls == []
    monkeypatch.setattr(strategies, "active", lambda db: True)
    out = autonomy.admission(db, T0, path="idle") if autonomy.enable(db, T0) else None
    assert calls == [1] and out["strategy"]["rule"] == "thompson"


@pytest.mark.parametrize("value", [1, "retain", ["retain"], {"decision": "retain"}, 0.5])
def test_a_truthy_non_boolean_gate_never_ranks_by_strategy(db, monkeypatch, caplog, value):
    """Wave 2 lane X (lane U's observation): only `True` from strategies.active opens the gate. A truthy value of
    another type is a failure of the gate: the admitted order stands, the admission says so, a warning is logged, and
    the self-description does not call strategy selection active."""
    from neuroscifi import self_description
    calls = []
    monkeypatch.setattr(strategies, "rank", lambda db, now, chosen: (calls.append(1) or list(reversed(chosen)),
                                                                      {"rule": "thompson"}))
    autonomy.enable(db, T0)
    reference = autonomy.admission(db, T0, path="idle")
    monkeypatch.setattr(strategies, "active", lambda db: value)
    out = autonomy.admission(db, T0, path="idle")
    assert calls == []
    assert out["chosen"] == reference["chosen"]
    assert "not a boolean" in out["strategy"]["error"]
    assert any("not true or false" in r.getMessage() for r in caplog.records)
    record = self_description._improvement_ledger(db, T0)
    assert record["value"]["strategy_selection_active"] is False


# ---- background and CLI ------------------------------------------------------------------------------------------------------

def test_both_activities_are_registered_cognition_held_by_quiet():
    for name in ("tick.knowledge_recheck", "tick.reflection"):
        assert background.ACTIVITIES[name]["kind"] == "cognition"
        assert name in background.UNITS["neuroscifi-webcortex-tick.timer"]


def test_nsf_knowledge_lane_k_commands(db, capsys):
    from neuroscifi.cli import main
    _history(db)
    path = str(__import__("pathlib").Path(db.execute("PRAGMA database_list").fetchone()[2]))

    def run(*argv):
        capsys.readouterr()
        assert main(["knowledge", *argv, "--db", path]) == 0
        return json.loads(capsys.readouterr().out)

    assert run("rechecks", "--rank")["present"] is True
    assert run("reflect", "--since-days", "3")["id"] == 1
    assert run("reflections")["reflections"][0]["id"] == 1
    assert run("strategy")["active"] is False
    assert run("ledger")["out_of_scope"]
    assert "members" in run("compete", "numpy latest version")
