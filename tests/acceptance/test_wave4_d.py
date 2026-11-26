"""Wave 4 lane D: housekeeping left open by Wave 3's reviews (each test fails without its fix)."""
from contextlib import closing
import json
import random
import time

import pytest

from neuroscifi.acceptance.cognition import adversarial as adv
from neuroscifi.instance import checkpoints, governance, store
from neuroscifi.webcortex import autonomy, consolidate, orchestrator, scheduler, state

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


# ---- item 3: a checkpoint restore scans the answers before its write transaction (review3c F6) --------------------

def _bulk_answers(db, n, size, rnd):
    """n stored model-written answers of about `size` characters, full of numbers (none about the staged subject)."""
    task = orchestrator.submit(db, adv._SQ, T0 - 2e6, mode="fact_verification", origin="console")
    cols = [c[1] for c in db.execute("PRAGMA table_info(web_tasks)")]
    row = dict(zip(cols, db.execute("SELECT * FROM web_tasks WHERE id=?", (task["id"],)).fetchone()))
    filler = " ".join(f"Item{i} {rnd.randint(100, 99999)}.{rnd.randint(0, 9)} was noted." for i in range(size // 15))
    result = json.dumps({"reasoning": {"generated_by": "model:x", "text": filler[:size]}})
    with store.write(db):
        for i in range(n):
            r = dict(row, id=f"bulk{i:07d}", result=result, updated_at=T0 - 1e6 + i, finished_at=T0 - 1e6 + i)
            db.execute(f"INSERT INTO web_tasks({','.join(r)}) VALUES({','.join('?' * len(r))})", tuple(r.values()))


def test_a_checkpoint_restore_reads_no_task_result_by_scanning_inside_its_write_transaction(tmp_path, monkeypatch):
    """The second fix lane moved the live forget's scan of every answer before its lock (review3b N2); a checkpoint
    replay still ran it inside the restore's transaction (1.5-1.7 s at 10,000 answers of 20 kB; review3c F6). Now it
    runs before it, and the mark inside reads only the candidates by rowid. The mark itself is unchanged (the M3
    replay test in test_wave3_fix.py, and the last lines here)."""
    rnd = random.Random(5)
    name = adv._SN
    with adv._s_individual("restore-scan") as (db, path):
        adv._s_look(db, T0 - 5 * DAY, [("relnotes.example", f"{name} 1.5.0 is the latest {name} release.",
                                        T0 - 6 * DAY, None)], answer=f"{name} 1.5.0 is the latest {name} release.")
        _bulk_answers(db, 10_000, 2_000, rnd)
        made = checkpoints.create(db, path, tmp_path / "checkpoints", T0 - 4.5 * DAY)
        governance.forget_source(db, "relnotes.example", T0 - 4 * DAY)
        full_scans, inside = [], []
        real_rows, real_replay = governance._answer_rows, governance.replay_web_forgets

        def rows(conn, tokens, rowids=None):
            if rowids is None:
                full_scans.append(conn.in_transaction)
            return real_rows(conn, tokens, rowids)

        def replay(conn, *a, **k):
            started = time.perf_counter()
            try:
                return real_replay(conn, *a, **k)
            finally:
                inside.append(time.perf_counter() - started)
        monkeypatch.setattr(governance, "_answer_rows", rows)
        monkeypatch.setattr(governance, "replay_web_forgets", replay)
        copy = tmp_path / "restored.sqlite"
        checkpoints.restore(db, made["id"], copy, T0 - 3 * DAY)
        assert full_scans and True not in full_scans, full_scans
        assert inside and max(inside) < 1.0, inside
        with closing(store.connect(copy)) as restored:
            assert restored.execute("SELECT count(*) FROM web_claims WHERE domain='relnotes.example'"
                                    ).fetchone()[0] == 0
            answers = [json.loads(r)["reasoning"] for (r,) in restored.execute(
                "SELECT result FROM web_tasks WHERE result LIKE ? AND id NOT LIKE 'bulk%'", ("%model:%",))]
            assert answers and all(consolidate.own_sentences(r) == [] for r in answers)
