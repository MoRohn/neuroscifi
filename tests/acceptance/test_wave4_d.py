"""Wave 4 lane D: housekeeping left open by Wave 3's reviews (each test fails without its fix)."""
from contextlib import closing

import pytest

from neuroscifi.acceptance.cognition import adversarial as adv
from neuroscifi.instance import store
from neuroscifi.webcortex import autonomy, orchestrator, scheduler, state

T0, DAY = adv.T0, adv.DAY


# ---- item 2: requeue honours the day's autonomous task count (review3b L6/N5, (f)) --------------------------------

def _capped(tmp_path, cap):
    db = store.create(tmp_path / "requeue.sqlite", T0, note="wave 4 lane D requeue")
    state.set_config(db, {"enabled": True, "daily_autonomous": {"tasks": cap, "requests": 400,
                                                                "bytes": 80_000_000}}, T0)
    return db


def _ended(db, task_id, status, at):
    with store.write(db):
        db.execute("UPDATE web_tasks SET status=?, created_at=?, updated_at=?, started_at=?, finished_at=? WHERE id=?",
                   (status, at, at, at, at, task_id))


def test_requeue_never_queues_an_autonomous_task_past_the_days_count(tmp_path):
    with closing(_capped(tmp_path, 2)) as db:
        old = orchestrator.submit(db, "What is the current Zorbix version?", T0 - 3 * DAY, mode="fact_verification",
                                  origin="curiosity")["id"]
        part = orchestrator.submit(db, "What is the current Quorvex version?", T0 - 3 * DAY,
                                   mode="fact_verification", origin="curiosity")["id"]
        _ended(db, old, "failed", T0 - 3 * DAY)
        _ended(db, part, "partial", T0 - 3 * DAY)
        for i in range(2):              # today's two places are taken
            orchestrator.submit(db, f"What is the current Racer{i} version?", T0, mode="fact_verification",
                                origin="curiosity")
        assert scheduler.autonomous_usage(db, T0 + 1)["tasks"] == 2
        with pytest.raises(autonomy.BudgetRefused):         # the same task, queued again from three days ago
            orchestrator.requeue(db, old, T0 + 1)
        with pytest.raises(autonomy.BudgetRefused):         # a child that keeps the autonomous origin
            orchestrator.requeue(db, part, T0 + 1)
        assert scheduler.autonomous_usage(db, T0 + 2)["tasks"] == 2
        assert state.task(db, old)["status"] == "failed"


def test_requeue_of_the_owners_work_is_never_counted_or_refused(tmp_path):
    with closing(_capped(tmp_path, 1)) as db:
        mine = orchestrator.submit(db, "Owner question: what is the latest Zorbix release?", T0 - 3 * DAY,
                                   mode="fact_verification", origin="console")["id"]
        theirs = orchestrator.submit(db, "What is the current Quorvex version?", T0 - 3 * DAY,
                                     mode="fact_verification", origin="curiosity")["id"]
        _ended(db, mine, "failed", T0 - 3 * DAY)
        _ended(db, theirs, "partial", T0 - 3 * DAY)
        orchestrator.submit(db, "What is the current Racer version?", T0, mode="fact_verification", origin="curiosity")
        assert orchestrator.requeue(db, mine, T0 + 1, origin="console")["requeued"] == "same"
        # the owner continuing a background task from the console queues the owner's own work (origin console)
        assert orchestrator.requeue(db, theirs, T0 + 1, origin="console")["requeued"] == "child"
        assert scheduler.autonomous_usage(db, T0 + 2)["tasks"] == 1


def test_requeue_of_an_autonomous_task_already_counted_today_is_not_refused(tmp_path):
    with closing(_capped(tmp_path, 1)) as db:
        today = orchestrator.submit(db, "What is the current Zorbix version?", T0, mode="fact_verification",
                                    origin="curiosity")["id"]
        _ended(db, today, "failed", T0)
        assert orchestrator.requeue(db, today, T0 + 1)["requeued"] == "same"      # it holds its place already
        assert scheduler.autonomous_usage(db, T0 + 2)["tasks"] == 1
