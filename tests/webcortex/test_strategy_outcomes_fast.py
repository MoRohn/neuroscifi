"""strategies.outcomes at scale (Wave 3 fix lane, from lane H's measurement: 5.8 s at 10,000 knowledge items, 500,000
events and 20,000 predictions, two unindexed look-ups per task). Rewritten over one pass per table, without an index
or a schema change: the same counts as the per-task query, in well under a second."""
import random
import time

from neuroscifi.acceptance.cognition import adversarial as adv
from neuroscifi.instance import store
from neuroscifi.webcortex import strategies
from neuroscifi.webcortex.types import MODES


def _copies(db, table, template_id, rows):
    cols = [r[1] for r in db.execute(f"PRAGMA table_info({table})")]
    base = dict(zip(cols, db.execute(f"SELECT * FROM {table} WHERE id=?", (template_id,)).fetchone()))
    for i, change in enumerate(rows):
        row = dict(base, **change)
        if table != "web_tasks":
            row.pop("id")
        db.execute(f"INSERT INTO {table}({','.join(row)}) VALUES({','.join('?' * len(row))})", tuple(row.values()))


def _per_task(db, window=strategies.WINDOW):
    """The query outcomes() ran before (one EXISTS pair per task)."""
    out = {}
    for mode in MODES:
        rows = db.execute(f"SELECT {strategies._REWARD} FROM web_tasks t WHERE t.mode=? AND t.status IN "
                          "('completed','partial') ORDER BY coalesce(t.finished_at, t.updated_at) DESC, t.rowid DESC "
                          "LIMIT ?", (mode, int(window))).fetchall()
        out[mode] = (len(rows), sum(1 for (r,) in rows if r))
    return out


def _staged(db, tasks, knowledge_rows, predictions):
    rng = random.Random(7)
    adv._s_item(db)
    task = db.execute("SELECT id FROM web_tasks").fetchone()[0]
    knowledge = db.execute("SELECT id FROM web_knowledge").fetchone()[0]
    prediction = db.execute("SELECT id FROM web_predictions").fetchone()[0]
    ids = [f"scale{i:06d}" for i in range(tasks)]
    with store.write(db):
        _copies(db, "web_tasks", task, [{"id": t, "mode": MODES[i % len(MODES)], "status": "completed",
                                          "finished_at": adv.T0 + i} for i, t in enumerate(ids)])
        _copies(db, "web_knowledge", knowledge, [{"key": f"scale-{i}", "version": 1,
                                                  "task_id": rng.choice(ids) if i % 3 else None,
                                                  "provenance": "verified_conclusion" if i % 2 else "user_asserted"}
                                                 for i in range(knowledge_rows)])
        _copies(db, "web_predictions", prediction, [{"task_id": rng.choice(ids), "outcome": rng.choice(
            ["confirmed", "error", None, None, "inconclusive"])} for _ in range(predictions)])


def test_outcomes_count_as_the_per_task_query_did():
    with adv._s_individual("outcomes-same") as (db, _):
        _staged(db, 2_000, 2_000, 4_000)
        before = _per_task(db)
        assert {m: (v["tasks"], v["rewarded"]) for m, v in strategies.outcomes(db).items()} == before
        assert any(r for _, r in before.values()) and any(t > r for t, r in before.values())


def test_outcomes_are_fast_at_ten_thousand_tasks():
    with adv._s_individual("outcomes-scale") as (db, _):
        _staged(db, 10_000, 10_000, 20_000)
        started = time.perf_counter()
        strategies.outcomes(db)
        elapsed = time.perf_counter() - started
        assert elapsed < 1.0, elapsed
