"""Controlled autonomy (D4): opt-in, the two daily budgets, the concurrency cap across processes, day rollover,
prioritisation from the records, the minimum-gain refusal, crash recovery and meaningful-finding notifications.

Real SQLite throughout; research runs offline against FixtureWeb + CorpusProvider (fake clock, explicit `now`)."""
from contextlib import closing
from datetime import datetime, timezone
import multiprocessing
import queue
import time
from types import SimpleNamespace

import pytest

from neuroscifi.instance import store
from neuroscifi.webcortex import autonomy, evolution, orchestrator, scheduler, state
from neuroscifi.webcortex.types import Budget
import race_helper
from test_orchestrator import PAGES, T0, build, corpus, open_individual, serve  # noqa: F401

DAY = 86400.0
STATEMENT = "The Zephyr compiler version 4.2.0 was released on 17 November 2026"


@pytest.fixture
def individual(tmp_path):
    yield from open_individual(tmp_path)


@pytest.fixture
def site(web):
    return serve(web)


def hypothesize(db, statement, p, now=T0):
    """The owner's hypothesis with a testable prediction (as `nsf knowledge hypothesize`)."""
    from neuroscifi.webcortex import claims, text
    with store.write(db):
        hid, _ = evolution.open_hypothesis(
            db, key=claims.claim_key(statement), statement=statement, origin="owner",
            verification={"policy": "ADR-035", "state": "not_evaluated", "basis": "owner_hypothesis",
                          "confidence": p}, claims=[], values=text.values(statement),
            polarity=claims.polarity(statement), now=now, with_prediction=False)
        pid = evolution.predict(db, hid, now, deadline=now + 20 * DAY, probability=p)
        store.bump_state_version(db)
    return hid, pid


def start(db, task_id, now, holder="worker-a"):
    """Start a queued task as a worker would (orchestrator._claim: the D4 admission is inside its transaction)."""
    services = SimpleNamespace(holder=holder, clock=lambda: now)
    return orchestrator._claim(db, task_id, services, resume=False)


def ledger(db, task_id):
    row = db.execute("SELECT scope, day, state, allowed, used FROM autonomy_ledger WHERE task_id=?",
                     (task_id,)).fetchone()
    return None if row is None else {"scope": row[0], "day": row[1], "state": row[2],
                                     "allowed": state.loads(row[3], {}), "used": state.loads(row[4], {})}


def curious(db, objective, now=T0, mode="fact_verification"):
    return orchestrator.submit(db, objective, now, mode=mode, origin="curiosity")


# ---- opt-in ----------------------------------------------------------------------------------------------------------

def test_autonomy_is_off_by_default_and_the_owner_enable_is_recorded(individual):
    db, _ = individual
    status = autonomy.status(db, T0)
    assert status["switch"] is False and status["enabled"] is False and status["record"] == {}
    assert status["autonomous"]["limits"] == {"searches": 20, "pages": 60, "seconds": 1800.0, "spend_usd": 1.0,
                                              "concurrent": 2}
    assert status["user"]["limits"]["searches"] == 400 and "concurrent" not in status["user"]["limits"]
    assert autonomy.idle(db, T0) == {"action": "off"}
    state.set_config(db, {"autonomous": True}, T0)          # the switch alone, without a recorded owner enable
    assert autonomy.enabled(db) is False and autonomy.idle(db, T0) == {"action": "off"}
    record = autonomy.enable(db, T0 + 1, by="owner:test")
    assert record["enabled_by"] == "owner:test" and record["enabled_at"] == T0 + 1 and autonomy.enabled(db)
    autonomy.disable(db, T0 + 2)
    assert not autonomy.enabled(db) and state.get_config(db).autonomous is False
    logged = [e["autonomy"] for e in autonomy.status(db, T0 + 3)["decisions"]]
    assert logged[:2] == ["disabled", "enabled"]


def test_cli_and_console_switches_record_the_owner(individual):
    from neuroscifi.webcortex import cli
    db, _ = individual
    args = SimpleNamespace(enable=None, autonomous=True, pocket_research=None, browser=None, news_render=None,
                           search_browser_ua=None, set=None)
    cli._config(db, args, T0)
    assert autonomy.enabled(db) and autonomy.policy(db)["record"]["enabled_by"] == "owner:cli"


# ---- budgets ---------------------------------------------------------------------------------------------------------

def finish(db, task_id, now, **usage):
    """End a started task having used `usage` (queries, pages, seconds), as the orchestrator does when it finishes."""
    with store.write(db):
        state.update_task(db, task_id, now, status="completed", finished_at=now, lease_holder=None,
                          lease_expires_at=None, usage=usage)
        autonomy.settle(db, task_id, usage, {}, now)


USED = {"searches": "queries", "pages": "pages", "seconds": "seconds"}


@pytest.mark.parametrize("limit, value, field, first, second, word", [
    ("searches", 7, "max_queries", 6, 1, "searches: 7 used of 7"),
    ("pages", 10, "max_pages", 8, 2, "pages: 10 used of 10"),
    ("seconds", 200.0, "max_seconds", 120.0, 80.0, "active time: 200 s used of 200 s"),
    ("spend_usd", 1.0, "max_queries", 6, 4, "external spend: $1.00 used of $1.00"),
])
def test_each_autonomous_limit_clamps_waits_then_refuses(individual, limit, value, field, first, second, word):
    """Running tasks hold their allowance; a third waits for it (UAT T5-02: it was cancelled although nothing had
    been used) and is refused only when what was actually used spends the budget."""
    db, _ = individual
    changes = {"autonomous_daily": {limit: value, "concurrent": 5}}
    if limit == "spend_usd":
        changes["prices_usd"] = {"search": 0.1}
    autonomy.set_policy(db, changes, T0)
    a, b, c = (curious(db, f"Zephyr compiler question {n}") for n in range(3))
    assert start(db, a["id"], T0)["budget"][field] == first
    assert start(db, b["id"], T0, "worker-b")["budget"][field] == second    # clamped to what is left
    assert ledger(db, b["id"])["allowed"][{"max_queries": "searches", "max_pages": "pages",
                                           "max_seconds": "seconds"}[field]] == second
    assert start(db, c["id"], T0, "worker-c") is None                       # the rest is held: it waits
    waiting = state.task(db, c["id"])
    assert waiting["status"] == "queued" and waiting["lease_holder"] is None and ledger(db, c["id"]) is None
    assert scheduler.claim_next(db, "worker-c", T0) is None                 # not picked up again while held
    assert autonomy.refusal(db, "autonomous", T0) is None
    assert curious(db, "Zephyr compiler question 4")                         # a queued task holds nothing
    key = "searches" if limit == "spend_usd" else limit
    finish(db, a["id"], T0, **{USED[key]: first})
    finish(db, b["id"], T0, **{USED[key]: second})
    assert start(db, c["id"], T0, "worker-c") is None                       # actually used up: refused
    refused = state.task(db, c["id"])
    assert refused["status"] == "cancelled" and "daily autonomous research budget exhausted" in refused["error"]
    assert word in refused["error"] and ledger(db, c["id"]) is None
    with pytest.raises(autonomy.BudgetRefused, match=word.replace("$", r"\$")):   # and at submit, at once
        curious(db, "Zephyr compiler question 5")
    assert orchestrator.submit(db, "Zephyr compiler question 6", T0, mode="fact_verification")   # user unaffected
    assert autonomy.status(db, T0)["autonomous"]["refusal"]


def test_a_waiting_task_starts_with_what_finished_tasks_left(individual):
    """T5-02: with 3 workers, tasks were cancelled 'searches 5 of 5' after 2 searches were used."""
    db, _ = individual
    autonomy.set_policy(db, {"user_daily": {"searches": 5}}, T0)
    tasks = [orchestrator.submit(db, f"Zephyr compiler question {n}", T0, mode="quick_lookup", origin="console")
             for n in range(3)]
    started = [start(db, t["id"], T0, f"worker-{n}") for n, t in enumerate(tasks)]
    assert started[0] and started[1] and started[2] is None
    assert state.task(db, tasks[2]["id"])["status"] == "queued"
    held = autonomy.status(db, T0)["user"]
    assert held["committed"]["searches"] == 5 and held["used"]["searches"] == 0 and held["refusal"] is None
    finish(db, tasks[0]["id"], T0, queries=1)
    finish(db, tasks[1]["id"], T0, queries=1)
    assert scheduler.claim_next(db, "worker-2", T0)[0] == tasks[2]["id"]
    third = start(db, tasks[2]["id"], T0, "worker-2")
    assert third["status"] == "queued" and ledger(db, tasks[2]["id"])["allowed"]["searches"] == \
        min(3, Budget.from_json(tasks[2]["budget"]).max_queries)


def test_queued_autonomous_tasks_hold_no_byte_allowance_and_wait_instead_of_being_cancelled(individual):
    """T5-02: eight queued autonomous tasks reserved their whole byte budgets; four were cancelled with 0 bytes."""
    db, _ = individual
    autonomy.set_policy(db, {"autonomous_daily": {"concurrent": 8}}, T0)
    state.set_config(db, {"daily_autonomous": {"tasks": 50, "requests": 10_000, "bytes": 3_000_000}}, T0)
    tasks = [curious(db, f"Zephyr compiler question {n}") for n in range(8)]
    assert all(Budget.from_json(t["budget"]).max_bytes == 3_000_000 for t in tasks)   # none held by the queue
    assert start(db, tasks[0]["id"], T0)                       # holds the day's bytes while it runs
    assert start(db, tasks[1]["id"], T0, "worker-b") is None   # waits, not cancelled
    assert state.task(db, tasks[1]["id"])["status"] == "queued"
    assert scheduler.claim_next(db, "worker-b", T0) is None
    finish(db, tasks[0]["id"], T0, bytes=1000, requests=3)
    assert scheduler.claim_next(db, "worker-b", T0)[0] == tasks[1]["id"]
    second = start(db, tasks[1]["id"], T0, "worker-b")
    assert second and Budget.from_json(second["budget"]).max_bytes == 3_000_000 - 1000
    assert not db.execute("SELECT count(*) FROM web_tasks WHERE status='cancelled'").fetchone()[0]


def test_user_research_has_its_own_budget(individual):
    db, _ = individual
    autonomy.set_policy(db, {"autonomous_daily": {"searches": 6}, "user_daily": {"searches": 6}}, T0)
    auto = curious(db, "Zephyr compiler boards")
    user = orchestrator.submit(db, "Zephyr compiler targets", T0, mode="fact_verification", origin="console")
    assert start(db, auto["id"], T0)["budget"]["max_queries"] == 6
    assert start(db, user["id"], T0, "worker-b")["budget"]["max_queries"] == 6   # autonomy spent nothing of it
    assert ledger(db, auto["id"])["scope"] == "autonomous" and ledger(db, user["id"])["scope"] == "user"
    finish(db, auto["id"], T0, queries=6)
    finish(db, user["id"], T0, queries=6)
    with pytest.raises(autonomy.BudgetRefused, match="daily user research budget"):
        orchestrator.submit(db, "Zephyr compiler release date", T0, mode="quick_lookup", origin="pocket")
    with pytest.raises(autonomy.BudgetRefused, match="daily autonomous research budget"):
        curious(db, "Zephyr compiler licence")
    status = autonomy.status(db, T0)
    assert status["user"]["committed"]["searches"] == 6 and status["autonomous"]["committed"]["searches"] == 6


def test_a_finished_task_settles_to_what_it_used_and_forgetting_refunds_nothing(individual, site):
    db, path = individual
    autonomy.set_policy(db, {"autonomous_daily": {"searches": 20}}, T0)
    task = curious(db, "What is the latest version of the Zephyr compiler?")
    services = build(db, path, site)
    try:
        done = orchestrator.run(db, task["id"], services)
    finally:
        services.close()
    row = ledger(db, task["id"])
    assert done["status"] in ("completed", "partial") and row["state"] == "settled"
    assert row["used"]["searches"] == done["usage"]["queries"] >= 1 and row["used"]["pages"] == done["usage"]["pages"]
    before = autonomy.status(db, T0)["autonomous"]["committed"]
    assert before["searches"] == row["used"]["searches"]
    from neuroscifi.instance import governance
    governance.forget_research(db, task["id"], T0 + 5)
    assert db.execute("SELECT count(*) FROM web_tasks").fetchone()[0] == 0
    assert autonomy.status(db, T0)["autonomous"]["committed"] == before
    # R6 D11: the ledger keeps the numbers but no longer names the task, and its times are only the day
    assert ledger(db, task["id"]) is None
    (key, day, opened, settled, used), = db.execute("SELECT task_id, day, opened_at, settled_at, used FROM "
                                                     "autonomy_ledger").fetchall()
    assert key.startswith("forgotten:") and task["id"] not in key and state.loads(used, {}) == row["used"]
    midnight = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp()
    assert opened == settled == midnight
    for table, in db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
        if table in ("deletion_log",):        # names the forgotten id by design (docs/governance)
            continue
        cols = [c[1] for c in db.execute(f"PRAGMA table_info({table})")]
        for col in cols:
            for needle in (task["id"], "Zephyr"):       # the id, and the objective an autonomy finding quoted
                assert db.execute(f"SELECT count(*) FROM {table} WHERE CAST({col} AS TEXT) LIKE ?",
                                  (f"%{needle}%",)).fetchone()[0] == 0, (table, col, needle)


def test_day_rollover_uses_the_explicit_now(individual):
    db, _ = individual
    autonomy.set_policy(db, {"autonomous_daily": {"searches": 6}}, T0)
    midnight = datetime.fromtimestamp(T0, timezone.utc).replace(hour=0, minute=0, second=0).timestamp() + DAY
    late = midnight - 60
    first = curious(db, "Zephyr compiler boards", late)
    assert start(db, first["id"], late)["budget"]["max_queries"] == 6
    finish(db, first["id"], late, queries=6)
    with pytest.raises(autonomy.BudgetRefused):
        curious(db, "Zephyr compiler targets", late + 30)
    assert autonomy.status(db, late)["day"] != autonomy.status(db, midnight + 1)["day"]
    second = curious(db, "Zephyr compiler targets", midnight + 1)            # a new UTC day: a fresh budget
    assert start(db, second["id"], midnight + 1, "worker-b")["budget"]["max_queries"] == 6
    assert ledger(db, first["id"])["day"] != ledger(db, second["id"])["day"]


# ---- concurrency across processes --------------------------------------------------------------------------------

def test_two_concurrent_autonomous_investigations_at_most_across_processes(individual):
    db, path = individual
    state.set_config(db, {"daily_autonomous": {"tasks": 50, "requests": 10_000, "bytes": 10**10}}, T0)  # 0.6 caps
    for n in range(6):
        curious(db, f"Zephyr compiler question {n}")
    # Started fresh, not forked (race_helper.py says why): six separate processes on one database file.
    # Timing (a slow runner): each process reports what happened, an error included, and the test waits on those
    # reports against one generous deadline rather than on fixed joins; the race itself is decided by SQLite's write
    # lock, not by timing, so a longer wait proves the same thing.
    ctx = multiprocessing.get_context("spawn")
    barrier, out = ctx.Barrier(6), ctx.Queue()
    procs = [ctx.Process(target=race_helper.race, args=(path, n, T0, barrier, out)) for n in range(6)]
    for p in procs:
        p.start()
    reports, deadline = {}, time.monotonic() + race_helper.BARRIER_TIMEOUT + 60
    try:
        while len(reports) < len(procs) and time.monotonic() < deadline:
            ended = not any(p.is_alive() for p in procs)     # read before the get: a report sent before the
            try:                                               # end is in the pipe by then
                n, tid, error = out.get(timeout=1.0)
            except queue.Empty:
                if ended:
                    break                   # every process ended; a missing report is a failure below
                continue
            reports[n] = (tid, error)
        for p in procs:
            p.join(max(0.0, deadline - time.monotonic()))
    finally:
        # A process that did not finish must not outlive the test: it would hold the runner's log pipe until the CI
        # job's timeout.
        stuck = [p for p in procs if p.is_alive()]
        for p in stuck:
            p.kill()
            p.join(5)
    assert not stuck, f"{len(stuck)} of {len(procs)} racing processes never finished (killed)"
    errors = {n: error for n, (_, error) in reports.items() if error}
    assert not errors, errors
    assert sorted(reports) == list(range(6)), f"reports from {sorted(reports)} only"
    started = [tid for tid, _ in reports.values() if tid]
    assert len(started) == 2
    assert autonomy.running(db, T0) == 2 and autonomy.full(db, T0)
    queued = db.execute("SELECT count(*) FROM web_tasks WHERE status='queued' AND lease_holder IS NOT NULL").fetchone()
    assert queued[0] == 2      # the two started ones hold their lease; the four others wait, unclaimed
    assert scheduler.claim_next(db, "proc-x", T0) is None
    user = orchestrator.submit(db, "Zephyr compiler targets", T0, mode="quick_lookup")
    assert scheduler.claim_next(db, "proc-x", T0)[0] == user["id"]   # user research is not held back


# ---- prioritisation and the gain threshold -----------------------------------------------------------------------

def test_priority_uses_goals_hypotheses_and_uncertainty_and_refuses_low_gain(individual):
    db, _ = individual
    scheduler.monitor_add(db, "Zephyr compiler release", T0, interval=6 * 3600, authorized_by="owner")
    hypothesize(db, STATEMENT, 0.5)
    hypothesize(db, "The Orchid database supports 12 replicas in its 3.1 version", 0.5)
    hypothesize(db, "The Quartz editor licence changed to MIT in 2025", 0.995)       # nearly certain: no gain
    ranked = autonomy.candidates(db, T0)
    keys = [c["statement"] for c in ranked["chosen"]]
    assert keys[0] == STATEMENT, "the goal-related hypothesis ranks first"
    top, other = ranked["chosen"][0], ranked["chosen"][1]
    assert top["signals"]["goal"] > 0 and other["signals"]["goal"] == 0
    assert top["signals"]["hypothesis"] == 1.0 and top["signals"]["prediction_uncertainty"] == 1.0
    assert top["priority"] > other["priority"] and top["info_gain"] == other["info_gain"]
    low = next(c for c in ranked["refused"] if "Quartz" in c["statement"])
    assert low["info_gain"] < ranked["min_gain"] and "no browsing to generate activity" in low["reason"]


def test_idle_refuses_when_nothing_clears_the_minimum_gain(individual):
    db, _ = individual
    hypothesize(db, "The Quartz editor licence changed to MIT in 2025", 0.995)
    autonomy.enable(db, T0)
    out = autonomy.idle(db, T0)
    assert out["action"] == "nothing" and "minimum" in out["reason"]
    assert db.execute("SELECT count(*) FROM web_tasks").fetchone()[0] == 0
    assert autonomy.idle(db, T0 + 1)["action"] == "waiting"        # one decision per idle_interval


# ---- the idle investigator end to end ----------------------------------------------------------------------------

def test_worker_picks_up_an_idle_investigation_and_notifies_only_meaningful_findings(individual, site):
    db, path = individual
    hid, pid = hypothesize(db, STATEMENT, 0.5)
    autonomy.enable(db, T0)
    autonomy.set_policy(db, {"idle_interval": 0.0}, T0)
    out = scheduler.work(path, once=True, fixture=site, providers=[corpus()], clock=lambda: T0 + 60, max_tasks=1)
    assert len(out["processed"]) == 1
    task = state.task(db, out["processed"][0]["task_id"])
    assert task["origin"] == "curiosity" and task["status"] in ("completed", "partial")
    inputs = next(e["detail"]["autonomy"] for e in state.events(db, task["id"])
                  if e["kind"] == "decision" and isinstance(e["detail"].get("autonomy"), dict))
    assert inputs["hypothesis_id"] == hid and inputs["prediction_id"] == pid and inputs["priority"] > 0
    assert set(inputs["signals"]) >= {"goal", "hypothesis", "prediction_uncertainty", "source_change"}
    status = autonomy.status(db, T0 + 60)["autonomous"]
    assert status["used"]["searches"] >= 1 and status["used"]["pages"] >= 1 and status["open"] == 0
    assert status["remaining"]["searches"] == 20 - status["used"]["searches"]
    assert evolution.prediction(db, pid)["outcome"] == "confirmed"
    question = db.execute("SELECT question FROM pending WHERE scope=?", (f"web:autonomy:{task['id']}",)).fetchone()
    assert question and "prediction(s) settled" in question[0]
    # The run left new records (single-source gaps, held subjects). Known only from autonomous research and unrelated
    # to an owner goal, they are not pursued; the cooled-down hypothesis is not investigated again.
    assert any("own by-products" in c["reason"] for c in autonomy.candidates(db, T0 + 120)["refused"])
    again = scheduler.work(path, once=True, fixture=site, providers=[corpus()], clock=lambda: T0 + 120)
    keys = [next(e["detail"]["autonomy"]["key"] for e in state.events(db, p["task_id"])
                 if e["kind"] == "decision" and isinstance(e["detail"].get("autonomy"), dict))
            for p in again["processed"] if not p["resumed"]]
    assert inputs["key"] not in keys and len(keys) == len(set(keys))
    after = autonomy.status(db, T0 + 120)["autonomous"]
    assert after["used"]["searches"] <= 20 and after["used"]["pages"] <= 60
    refused = [d for d in autonomy.status(db, T0 + 120)["decisions"] if d.get("autonomy") in
               ("refused", "nothing worth researching")]
    assert refused, "the worker stopped for a stated reason (budget or minimum gain), not silently"


def test_a_hypothesis_settles_on_its_autonomous_tasks_link_when_the_keys_differ(individual, site):
    """Live Scenario 6: the research question shifts the cluster key ("compiler releas version zephyr") away from
    the hypothesis key ("releas zephyr"; Jaccard 0.5 < SIMILAR_KEY), so lexical matching alone never settled the
    prediction. The autonomous task carries the hypothesis id and settlement follows that link:
    hypothesis -> autonomous task -> supported cluster -> prediction confirmed -> calibration -> notification."""
    from neuroscifi.webcortex.consolidate import SIMILAR_KEY, _similar
    db, path = individual
    statement = "Zephyr 4.2.0 was released on 17 November 2026"
    hid, pid = hypothesize(db, statement, 0.6)
    assert evolution.hypothesis(db, hid)["key"] == "releas zephyr"
    autonomy.enable(db, T0)
    autonomy.set_policy(db, {"idle_interval": 0.0}, T0)
    assert evolution.calibration_history(db) == []
    out = scheduler.work(path, once=True, fixture=site, providers=[corpus()], clock=lambda: T0 + 60, max_tasks=1)
    task = state.task(db, out["processed"][0]["task_id"])
    assert task["origin"] == "curiosity" and task["status"] in ("completed", "partial")
    assert task["checkpoint"]["context"] == {"hypothesis": hid}                # the explicit link
    pred = evolution.prediction(db, pid)
    assert pred["outcome"] == "confirmed" and pred["task_id"] == task["id"]
    seen = pred["detail"]["observation"]
    assert seen["match"] == "linked" and seen["state"] == "supported" and seen["relation"] == "same"
    assert _similar(seen["key"], "releas zephyr") < SIMILAR_KEY, "the lexical match alone would not settle it"
    h = evolution.hypothesis(db, hid)
    assert h["status"] == "supported" and h["resolved_at"] == T0 + 60
    history = evolution.calibration_history(db)
    assert history and history[0]["n"] == 1 and history[0]["brier"] == pytest.approx((0.6 - 1.0) ** 2)
    assert task["checkpoint"]["predictions_settled"] == [{"prediction_id": pid, "hypothesis_id": hid,
                                                         "outcome": "confirmed"}]
    question = db.execute("SELECT question FROM pending WHERE scope=?", (f"web:autonomy:{task['id']}",)).fetchone()
    assert question and f"#{pid} confirmed" in question[0]


def test_the_lexical_fallback_never_settles_an_unrelated_hypothesis_on_a_link(individual):
    """The link names one hypothesis: another open hypothesis whose key the task's clusters do not match stays
    open, and the linked one is not settled by a cluster naming only half of its key tokens."""
    from types import SimpleNamespace as NS
    db, _ = individual
    hid, pid = hypothesize(db, "Zephyr 4.2.0 was released on 17 November 2026", 0.6)
    other, opid = hypothesize(db, "The Orchid database supports 12 replicas in its 3.1 version", 0.5)
    task = orchestrator.submit(db, "Zephyr release", T0, mode="fact_verification", origin="curiosity",
                               context={"hypothesis": hid})
    unrelated = NS(key="orchid zephyr replica databas", claims=[], supporting=[])
    with store.write(db):
        assert evolution.observe(db, task["id"], [(unrelated, {"state": "supported"}, "x")], T0 + 1) == []
    assert evolution.prediction(db, pid)["outcome"] is None and evolution.prediction(db, opid)["outcome"] is None
    with pytest.raises(Exception, match="hypothesis"):
        orchestrator.submit(db, "Zephyr release", T0, origin="curiosity", context={"hypothesis": "1"})


def test_a_finding_is_delivered_when_the_owners_question_budget_is_full(individual, site):
    """R1-01: with max_open_web_questions (10) already open, the confirmed prediction of the owner's hypothesis was
    logged "notified" with pending_id null and no question existed; the console said "You were notified". The
    notification is the reason autonomy runs, so it is delivered whatever the interruption budget, and "notified"
    is recorded only with the pending id it was delivered as."""
    from neuroscifi.webcortex import knowledge
    db, path = individual
    with store.write(db):
        for n in range(state.get_config(db).max_open_web_questions):
            knowledge.open_question(db, f"web:contested:filler:{n}", f"filler question {n}", T0)
    assert not knowledge.question_room(db)
    hid, pid = hypothesize(db, STATEMENT, 0.5)
    autonomy.enable(db, T0)
    autonomy.set_policy(db, {"idle_interval": 0.0}, T0)
    out = scheduler.work(path, once=True, fixture=site, providers=[corpus()], clock=lambda: T0 + 60, max_tasks=1)
    task = state.task(db, out["processed"][0]["task_id"])
    assert evolution.prediction(db, pid)["outcome"] == "confirmed"
    question = db.execute("SELECT id, question FROM pending WHERE scope=? AND resolved_at IS NULL",
                          (f"web:autonomy:{task['id']}",)).fetchone()
    assert question and f"#{pid} confirmed" in question[1]
    notified = [e["detail"] for e in state.events(db, task["id"])
                if e["kind"] == "decision" and e["detail"].get("autonomy") == "notified"]
    assert notified and notified[0]["pending_id"] == question[0]


def test_notified_is_never_recorded_without_a_delivered_question(individual, monkeypatch):
    """Should delivery fail, the decision says so ("not delivered"), never "notified" with a null id."""
    from neuroscifi.webcortex import knowledge
    db, _ = individual
    task = curious(db, "Zephyr compiler release blog")
    orchestrator.cancel(db, task["id"], T0 + 1)
    with store.write(db):
        db.execute("UPDATE web_tasks SET checkpoint=? WHERE id=?", (state.dumps({"predictions_settled": [
            {"prediction_id": 7, "hypothesis_id": 3, "outcome": "confirmed"}]}), task["id"]))
    monkeypatch.setattr(knowledge, "open_question", lambda *a, **k: None)
    found = autonomy.after_task(db, task["id"], T0 + 5)
    assert found["pending_id"] is None
    details = [e["detail"] for e in state.events(db, task["id"]) if e["kind"] == "decision"]
    assert not any(d.get("autonomy") == "notified" for d in details)
    assert any(d.get("autonomy") == "finding not delivered" for d in details)


def test_no_notification_without_a_meaningful_finding(individual):
    db, _ = individual
    task = curious(db, "Zephyr compiler release blog")
    orchestrator.cancel(db, task["id"], T0 + 1)          # finished, found nothing
    found = autonomy.after_task(db, task["id"], T0 + 5)
    assert found == {"new_verified": [], "resolved_contradictions": [], "settled_predictions": []}
    assert db.execute("SELECT count(*) FROM pending WHERE scope LIKE 'web:autonomy:%'").fetchone()[0] == 0
    assert any(e["detail"].get("autonomy") == "no meaningful finding: no notification"
               for e in state.events(db, task["id"]))
    user = orchestrator.submit(db, "Zephyr compiler targets", T0, mode="quick_lookup")
    orchestrator.cancel(db, user["id"], T0 + 1)
    assert autonomy.after_task(db, user["id"], T0 + 5) is None       # the owner's own research never notifies here


# ---- crash recovery ----------------------------------------------------------------------------------------------

def test_a_crashed_autonomous_task_resumes_without_a_second_allowance(individual, site):
    db, path = individual
    autonomy.set_policy(db, {"autonomous_daily": {"searches": 6, "concurrent": 1}}, T0)
    task = curious(db, "What is the latest version of the Zephyr compiler?")
    started = start(db, task["id"], T0, holder="deadhost:1:1:1")
    assert started and ledger(db, task["id"])["state"] == "open"
    with store.write(db):          # the worker crashed mid-run: running status, expired lease
        state.update_task(db, task["id"], T0, status="reading", stage="reading", lease_expires_at=T0 + 1)
    assert autonomy.full(db, T0 + 10)        # the crashed task still holds its (only) slot
    out = scheduler.work(path, once=True, fixture=site, providers=[corpus()], clock=lambda: T0 + 700)
    assert out["processed"][0] == {"task_id": task["id"], "status": out["processed"][0]["status"], "resumed": True}
    row = ledger(db, task["id"])
    assert row["state"] == "settled" and row["allowed"]["searches"] == 6
    assert db.execute("SELECT count(*) FROM autonomy_ledger").fetchone()[0] == 1


# ---- counting SearXNG and its engines ----------------------------------------------------------------------------

def test_search_counts_separate_searxng_requests_from_engine_requests():
    attempts = [
        {"provider": "searxng", "engine": "bing", "query": "q1", "ok": True, "n": 3, "error": None},
        {"provider": "searxng", "engine": "brave", "query": "q1", "ok": False, "n": 0, "error": "rate_limited: x"},
        {"provider": "searxng", "engine": "qwant", "query": "q1", "ok": False, "n": 0, "error": "circuit_open"},
        {"provider": "searxng", "engine": None, "query": "q1", "ok": True, "n": 3, "error": None},
        {"provider": "searxng", "engine": None, "query": "q2", "ok": True, "n": 2, "error": None},   # cache hit
        {"provider": "searxng", "engine": None, "query": "q2", "ok": True, "n": 2, "error": None},
        {"provider": "searxng", "engine": None, "query": "q3", "ok": False, "n": 0, "error": "timeout: slow"},
        {"provider": "wikipedia", "engine": None, "query": "q1", "ok": True, "n": 1, "error": None},
    ]
    assert autonomy.search_counts(attempts) == {"searxng_requests": 2, "engine_requests": 2}


def test_policy_validation(individual):
    db, _ = individual
    from neuroscifi.instance.errors import ValidationError
    for bad in ({"autonomous_daily": {"searches": -1}}, {"autonomous_daily": {"searches": 1.5}},
                {"autonomous_daily": {"tokens": 3}}, {"min_gain": 2}, {"budget": 1}, {},
                {"autonomous_daily": {"searches": float("inf")}}, {"user_daily": {"seconds": float("inf")}},
                {"cooldown": float("inf")}, {"prices_usd": {"search": float("nan")}}):   # T5-04: inf -> 500
        with pytest.raises(ValidationError):
            autonomy.set_policy(db, bad, T0)
    assert autonomy.set_policy(db, {"user_daily": {"pages": 5}}, T0)["user_daily"]["pages"] == 5


def test_ledger_rows_hold_no_text(individual):
    db, _ = individual
    task = curious(db, "Zephyr compiler secret-free question")
    start(db, task["id"], T0)
    with closing(db.execute("SELECT * FROM autonomy_ledger")) as cur:
        assert all("Zephyr" not in str(v) for row in cur for v in row)
