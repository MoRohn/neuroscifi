"""Wave 2 lane X: the second-wave security review's findings on lane U's guards and lane L's arbitration record
(review2 L2, L3, L4, Info 3). Each test fails without its fix. L1 (no exception message in a log or a stored event) is
tests/uat/test_uat_fault_injection.py::test_a_failure_message_naming_a_subject_reaches_no_log_and_no_stored_record;
M1 and L6 are in tests/uat/test_uat_voice_follow_faults.py; L5 is in test_knowledge_evolution.py."""
import random
import sqlite3

import pytest

from neuroscifi.acceptance.cognition import adversarial
from neuroscifi.instance import evidence, executive, store
from neuroscifi.webcortex import autonomy, curiosity, predictions, recheck, scheduler, state
from test_background import ranked
from test_orchestrator import T0, open_individual

DAY = 86_400.0
A = (1, "ostra basin area", "The Ostra basin covers 9,400 square kilometres of lowland.", 0.5)
B = (2, "varnel river length", "The Varnel River is 412 kilometres long and drains the Ostra basin.", 0.3)


@pytest.fixture
def individual(tmp_path):
    yield from open_individual(tmp_path)


def _two_items(db):
    with store.write(db):
        for kid, key, statement, _ in (A, B):
            db.execute("INSERT INTO web_knowledge(id,key,version,statement,status,confidence,uncertainty,"
                       "freshness_class,first_verified_at,last_verified_at,created_at,updated_at) "
                       "VALUES(?,?,1,?,'provisional',0.6,0.2,'stable',1,1,1,1)", (kid, key, statement))


def test_a_failing_learning_progress_read_falls_back_to_the_strict_futility_rule(individual, monkeypatch):
    """review2 L2: with curiosity_learning_progress on, a failing learning-progress read used the gentler futility
    rule (confirming refreshes count as progress), which damps less than the rule with the setting off. It now falls
    back to the rule with the setting off."""
    db, _ = individual
    _two_items(db)
    state.set_config(db, {"curiosity_learning_progress": True}, T0)

    def futility(db, key, *, refresh_is_progress=False):
        return (0.9, "; GENTLE") if refresh_is_progress else (0.25, "; STRICT")

    def progress(*args, **kwargs):
        raise RuntimeError("staged: learning progress cannot be read")
    monkeypatch.setattr(curiosity, "_futility", futility)
    monkeypatch.setattr(curiosity, "progress", progress)
    ranked_ = curiosity.rank(db, T0 + 400 * DAY, limit=10)
    mine = [c for c in ranked_ if c["target"].get("knowledge_id") in (A[0], B[0])]
    assert mine
    for c in mine:
        assert "STRICT" in c["reason"] and "GENTLE" not in c["reason"], c["reason"]
        assert c["learning_progress"]["error"] == "could not be read (RuntimeError)"


def _epoch(db):
    rng = random.Random("lane-x-epoch")
    evidence.start_epoch(db, "e", T0)
    for i in range(24):
        action = rng.randint(0, 1)
        evidence.observe(db, {"id": f"r-{i}", "epoch": "e", "channel": "reference" if i < 12 else "word",
                              "root": f"ref-{i % 3}" if i < 12 else "p", "action": action,
                              "bit": (i % 2) ^ action if i >= 12 else action, "provenance": "simulated",
                              "actor": "simulator"}, T0 + i)


def test_a_decision_made_by_the_rules_v1_fallback_says_so_where_it_is_stored(tmp_path, monkeypatch):
    """review2 L3: when information-v1 fails, rules-v1 decides; the stored decision (the row later audits read)
    says so, not only the live reply."""
    from neuroscifi.instance import information
    db = store.create(tmp_path / "individual.sqlite", T0)
    try:
        _epoch(db)
        store.set_config(db, {"executive_rule": "information-v1"}, T0)

        def fail(*args, **kwargs):
            raise RuntimeError("staged: information-v1 cannot decide")
        monkeypatch.setattr(information, "information_policy", fail)
        out = executive.decide(db, "e", T0 + 100, record=True)
        stored = db.execute("SELECT reason FROM decisions ORDER BY id DESC LIMIT 1").fetchone()[0]
        assert "information-v1 failed (RuntimeError); decided by rules-v1" in stored
        assert out["rule_error"] in stored and out["reason"] == stored
    finally:
        db.close()


def test_re_checks_queued_before_the_re_check_failed_still_count_in_the_pass(individual, monkeypatch):
    """review2 L4: a re-check pass that queued a task and then failed used to leave that task out of the pass's
    budget (max_tasks) and of the arbitration's record of what the re-check took."""
    db, _ = individual
    real_select, real_submit = recheck.select, recheck.submit
    adversarial._k_item(db, T0 - 30 * DAY)
    autonomy.enable(db, T0)
    state.set_config(db, {"autonomous": True, "idle_recheck": True}, T0)
    now = T0 + DAY
    queued = []

    def run(db, now, *, limit=None, over_cap=None):
        chosen = real_select(db, now, limit=1)
        queued.append(real_submit(db, chosen["picks"][0], chosen["admitted"], now)["id"])
        raise RuntimeError("staged: the re-check failed after queuing one task")
    monkeypatch.setattr(recheck, "run", run)
    report = scheduler.tick(db, now, None, queue=True, max_tasks=1)
    assert queued
    assert {"task_id": queued[0], "origin": "recheck"} in report["queued"]
    assert len(report["queued"]) == 1                                      # max_tasks 1: the re-check's task
    assert any(s.get("what") == "knowledge re-check" for s in report["skipped"])


def test_an_arbitration_record_that_cannot_be_written_never_costs_the_rest_of_the_pass(individual, monkeypatch):
    """review2 Info 3: the arbitration record (record only) opened its own write; when that failed, the timer's pass
    stopped before curiosity's launches and the cache retention. Now the pass goes on and the failure is in its
    report and the log."""
    db, _ = individual
    _two_items(db)
    monkeypatch.setattr(curiosity, "rank", ranked(A, B))
    refresh = [{"prediction_id": 1, "action": "refresh_submitted", "task_id": "refresh-task", "knowledge_id": A[0]}]
    monkeypatch.setattr(predictions, "check_due", lambda *a, **k: refresh)
    autonomy.enable(db, T0)
    real = state.log_event

    def log_event(db, task_id, kind, detail, now):
        if isinstance(detail, dict) and "arbitration" in detail:
            raise sqlite3.OperationalError("database is locked")
        return real(db, task_id, kind, detail, now)
    monkeypatch.setattr(state, "log_event", log_event)

    class Services:
        def prune_cache(self, now):
            return {"pruned": 0}
    report = scheduler.tick(db, T0, Services(), queue=True)
    assert {"what": "arbitration record", "task_id": "refresh-task", "reason": "failed: OperationalError"} \
        in report["skipped"]
    assert "arbitration" not in report
    assert [q["origin"] for q in report["queued"]].count("curiosity") == 1   # B, the other subject, still launched
    assert report["cache"] == {"pruned": 0}                                  # and the retention ran


# ---- Wave 2 reconciliation: the check of the fixes (review2b N5) --------------------------------------------------

MARKER = "SUBJECT-MARKER-7302"


def _sqlite_failure(kind, tmp_path):
    """A real sqlite3 error carrying the marker in its message: 'error' (SQLITE_ERROR, no such table) or 'readonly'
    (SQLITE_READONLY, a write on a read-only connection)."""
    import sqlite3
    path = tmp_path / f"{kind}.sqlite"
    sqlite3.connect(path).execute("CREATE TABLE t(x)").connection.commit()
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        db.execute(f"SELECT * FROM missing_{kind}" if kind == "error" else "INSERT INTO t VALUES (1)")
    except sqlite3.Error as exc:
        exc.args = (f"{exc.args[0]} about {MARKER}",)
        return exc
    finally:
        db.close()


def test_a_forecast_failure_is_logged_by_its_kind_with_its_repeats_and_no_subject(tmp_path, monkeypatch, caplog):
    """review2b N5: the gate logged a forecast failure once per exception TYPE per process, so a later failure of the
    same type with another cause was silent. It is logged once per kind (the type with SQLite's error name), and a
    repeat again at 10, 100, ... with its count; never the message."""
    import logging
    from neuroscifi import internal_state
    from neuroscifi.resources import policy
    caplog.set_level(logging.WARNING)
    policy._FORECAST_FAILURES.clear()
    failures = {"now": _sqlite_failure("error", tmp_path)}

    def history(*args, **kwargs):
        raise failures["now"]
    monkeypatch.setattr(policy, "predictive_on", lambda db_path: True)
    monkeypatch.setattr(internal_state, "history", history)
    gate = {"level": "ok", "reasons": []}
    for _ in range(10):
        assert policy.background_gate(tmp_path / "x.sqlite", sample=gate, audio=False)["allowed"]
    failures["now"] = _sqlite_failure("readonly", tmp_path)
    policy.background_gate(tmp_path / "x.sqlite", sample=gate, audio=False)
    logged = [r.getMessage() for r in caplog.records if "forecast failed" in r.getMessage()]
    assert logged == ["predictive regulation: the forecast failed (OperationalError SQLITE_ERROR); it adds no hold",
                      "predictive regulation: the forecast failed (OperationalError SQLITE_ERROR) 10 times in this "
                      "process; it adds no hold",
                      "predictive regulation: the forecast failed (OperationalError SQLITE_READONLY); it adds no "
                      "hold"], logged
    assert not [r for r in caplog.records if MARKER in r.getMessage()]
    policy._FORECAST_FAILURES.clear()


def test_a_failing_learning_progress_read_names_the_knowledge_item_never_its_subject(individual, monkeypatch, caplog,
                                                                                    tmp_path):
    """review2b N5: the curiosity warning named no item at all; it names the knowledge id and SQLite's error name,
    never the key or the message."""
    import logging
    caplog.set_level(logging.WARNING)
    db, _ = individual
    _two_items(db)
    state.set_config(db, {"curiosity_learning_progress": True}, T0)
    failure = _sqlite_failure("error", tmp_path)

    def progress(*args, **kwargs):
        raise failure
    monkeypatch.setattr(curiosity, "progress", progress)
    curiosity.rank(db, T0 + 400 * DAY, limit=10)
    logged = [r.getMessage() for r in caplog.records if "learning progress" in r.getMessage()]
    assert any("knowledge #1" in m and "OperationalError SQLITE_ERROR" in m for m in logged), logged
    assert not [m for m in logged if MARKER in m or A[1] in m or B[1] in m], logged
