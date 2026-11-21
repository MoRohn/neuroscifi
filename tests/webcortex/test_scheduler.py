"""Monitors, the timer tick (fake clock, offline fixture web) and the worker's lease handling."""
import threading

import pytest

from neuroscifi.instance import store
from neuroscifi.instance.errors import Conflict, ValidationError
from neuroscifi.webcortex import orchestrator, predictions, scheduler, state
from test_orchestrator import PAGES, T0, build, corpus, html, open_individual, serve

HOUR = 3600.0
DAY = 86400.0
TOPIC = "Zephyr compiler latest version"


@pytest.fixture
def individual(tmp_path):
    yield from open_individual(tmp_path)


@pytest.fixture
def site(web):
    return serve(web)


def republish(web, version, day):
    for (host, path), (title, _, paragraphs) in PAGES.items():
        new = [p.replace("4.2.0", version).replace("17 November", f"{day} November") for p in paragraphs]
        web.add(host, path, html(title.replace("4.2", version[:3]), f"2026-11-{day}T08:00:00Z", new))


def test_monitor_lifecycle_and_validation(individual):
    db, _ = individual
    mon = scheduler.monitor_add(db, TOPIC, T0, interval=2 * HOUR, authorized_by="owner")
    assert mon["status"] == "active" and mon["next_run_at"] == T0 and mon["pending_id"] is None
    assert scheduler.monitor_pause(db, mon["id"], T0)["status"] == "paused"
    assert scheduler.monitor_resume(db, mon["id"], T0)["status"] == "active"
    assert scheduler.monitor_stop(db, mon["id"], T0)["status"] == "stopped"
    with pytest.raises(Conflict):
        scheduler.monitor_resume(db, mon["id"], T0)
    proposed = scheduler.monitor_add(db, TOPIC, T0, interval=HOUR, authorized_by="pocket:" + "0f" * 8)
    assert proposed["status"] == "proposed" and proposed["pending_id"]
    question = db.execute("SELECT scope, question FROM pending WHERE id=?", (proposed["pending_id"],)).fetchone()
    assert question[0] == f"web:monitor:{proposed['id']}" and "every 1 h" in question[1]
    for bad in ({"interval": HOUR - 1, "authorized_by": "owner"}, {"interval": HOUR, "authorized_by": "pocket:x"},
                {"interval": HOUR, "authorized_by": "owner", "mode": "dreaming"}):
        with pytest.raises(ValidationError):
            scheduler.monitor_add(db, TOPIC, T0, **bad)
    with pytest.raises(ValidationError, match="private data"):
        scheduler.monitor_add(db, "news about jo@example.org", T0, interval=HOUR, authorized_by="owner")


def test_tick_runs_due_monitors_and_reports_meaningful_change(individual, site):
    db, path = individual
    mon = scheduler.monitor_add(db, TOPIC, T0, interval=6 * HOUR, authorized_by="owner")
    services = build(db, path, site)
    try:
        first = scheduler.tick(db, T0, services)
    finally:
        services.close()
    assert [r["origin"] for r in first["ran"]] == [f"monitor:{mon['id']}"] and first["findings"] == []
    assert any("autonomous research is off" in s["reason"] for s in first["skipped"])
    after = scheduler.monitor_list(db)[0]
    assert after["runs"] == 1 and after["next_run_at"] == T0 + 6 * HOUR and after["baseline"]["claims"]

    services = build(db, path, site, clock=lambda: T0 + HOUR)
    try:
        assert scheduler.tick(db, T0 + HOUR, services)["ran"] == []        # not due yet
        same = scheduler.tick(db, T0 + 6 * HOUR, services)                 # due; the web did not change
    finally:
        services.close()
    assert len(same["ran"]) == 1 and same["findings"] == []

    republish(site, "4.3.0", 21)
    later = T0 + 12 * HOUR
    services = build(db, path, site, clock=lambda: later)
    try:
        changed = scheduler.tick(db, later, services)
    finally:
        services.close()
    assert len(changed["findings"]) == 1
    finding = changed["findings"][0]["changes"]
    assert finding["meaningful"] and (finding["changed_values"] or finding["new_claims"] or finding["superseded"])
    events = db.execute("SELECT detail FROM web_events WHERE kind='finding'").fetchall()
    assert len(events) == 1 and f'"monitor_id":{mon["id"]}' in events[0][0]


def test_tick_autonomous_refreshes_due_predictions_within_the_daily_cap(individual, site):
    db, path = individual
    task = orchestrator.submit(db, "What is the latest version of the Zephyr compiler?", T0)
    services = build(db, path, site)
    try:
        done = orchestrator.run(db, task["id"], services)
    finally:
        services.close()
    kid = done["result"]["consolidation"]["provisional"][0]
    pred = predictions.listing(db, open_only=True)[0]
    assert pred["knowledge_id"] == kid and pred["check_after"] > T0
    state.set_config(db, {"autonomous": True, "daily_autonomous": {"tasks": 1}}, T0)
    due = pred["check_after"] + 1
    services = build(db, path, site, clock=lambda: due)
    try:
        report = scheduler.tick(db, due, services)
        assert [a["action"] for a in report["predictions"]] == ["refresh_submitted"]
        assert [r["origin"] for r in report["ran"]] == ["refresh"] and report["ran"][0]["status"] == "completed"
        assert scheduler.autonomous_usage(db, due)["tasks"] == 1
        settle = scheduler.tick(db, due + HOUR, services)
    finally:
        services.close()
    assert settle["predictions"] == [{"prediction_id": pred["id"], "action": "checked", "outcome": "confirmed"}]
    assert predictions.listing(db)[0]["knowledge_id"] == kid and predictions.listing(db)[0]["outcome"] is None
    assert all(r["origin"] != "curiosity" for r in settle["ran"])


def test_tick_respects_the_cap_for_curiosity_and_the_master_switch(individual, site):
    db, path = individual
    state.set_config(db, {"autonomous": True, "daily_autonomous": {"tasks": 0}}, T0)
    task = orchestrator.submit(db, "What is the latest version of the Zephyr compiler?", T0)
    services = build(db, path, site)
    try:
        orchestrator.run(db, task["id"], services)
        later = T0 + 30 * DAY
        report = scheduler.tick(db, later, services)
    finally:
        services.close()
    reasons = [s["reason"] for s in report["skipped"]]
    assert report["ran"] == [] and any("daily autonomous cap reached: tasks 0 of 0" in r for r in reasons)
    state.set_config(db, {"enabled": False}, T0)
    off = scheduler.tick(db, T0, None)
    assert off["ran"] == [] and "disabled" in off["skipped"][0]["reason"]


def test_worker_drains_the_queue_and_recovers_expired_leases(individual, site):
    db, path = individual
    queued = orchestrator.submit(db, "What is the latest version of the Zephyr compiler?", T0,
                                 mode="fact_verification")
    crashed = orchestrator.submit(db, "Zephyr compiler release date", T0, mode="quick_lookup")
    held = orchestrator.submit(db, "Zephyr compiler targets", T0, mode="quick_lookup")
    with store.write(db):
        state.update_task(db, crashed["id"], T0, status="reading", stage="reading", lease_holder="dead-worker",
                          lease_expires_at=T0 - 1)
        state.update_task(db, held["id"], T0, lease_holder="live-worker", lease_expires_at=T0 + 10 * HOUR)
    out = scheduler.work(path, once=True, fixture=site, providers=[corpus()], clock=lambda: T0 + 60)
    processed = {p["task_id"]: p for p in out["processed"]}
    assert set(processed) == {queued["id"], crashed["id"]}
    assert processed[crashed["id"]]["resumed"] is True and processed[queued["id"]]["resumed"] is False
    assert state.task(db, queued["id"])["status"] == "completed"
    assert state.task(db, crashed["id"])["status"] in ("completed", "partial")
    assert state.task(db, held["id"])["status"] == "queued"          # another worker's live lease is respected
    assert scheduler.work(path, once=True, fixture=site, providers=[corpus()], clock=lambda: T0 + 120) == {
        "processed": []}


def test_worker_polls_until_stopped(individual):
    _, path = individual
    stop, naps = threading.Event(), []

    def sleep(seconds):
        naps.append(seconds)
        if len(naps) == 2:
            stop.set()

    assert scheduler.work(path, stop=stop, sleep=sleep, poll=7.0, providers=[corpus()]) == {"processed": []}
    assert naps == [7.0, 7.0]


def test_prediction_check_outcomes(individual):
    from test_consolidate import consolidated, two_sources
    db, _ = individual
    _, first = consolidated(db, [two_sources(published=T0 - 10 * DAY)])
    kid = first["provisional"][0]
    confirmed = predictions.record(db, kid, "still holds at next refresh", T0 + DAY, T0)
    out = predictions.check(db, confirmed["id"], {"consolidation": {"unchanged": [kid]}}, T0 + 2 * DAY)
    assert out["outcome"] == "confirmed" and out["detail"]["next_prediction"]
    inconclusive = predictions.record(db, kid, "still holds at next refresh", T0 + DAY, T0)
    assert predictions.check(db, inconclusive["id"], {}, T0 + 2 * DAY)["outcome"] == "inconclusive"
    failing = predictions.record(db, kid, "still holds at next refresh", T0 + DAY, T0)
    _, second = consolidated(db, [two_sources(published=T0 - DAY, text="NumPy 2.4.0 is the latest release of NumPy.",
                                              hosts=("c.example", "d.example"))], T0 + DAY)
    error = predictions.check(db, failing["id"], {"consolidation": second}, T0 + 2 * DAY)
    assert error["outcome"] == "error" and "superseded" in error["detail"]["reason"]
    assert "pending_id" not in error["detail"]     # the successor version carries the open question
    assert db.execute("SELECT count(*) FROM web_events WHERE kind='prediction_error'").fetchone()[0] == 1
    assert db.execute("SELECT resolution FROM pending WHERE scope=?", (f"web:knowledge:{kid}",)).fetchone()[0] == \
        f"superseded by knowledge #{second['superseded'][0]['new']}"
    new = second["superseded"][0]["new"]
    contradicted = predictions.record(db, new, "still holds at next refresh", T0 + 3 * DAY, T0 + DAY)
    error = predictions.check(db, contradicted["id"], {"consolidation": {"contested": [
        {"knowledge_id": new, "reason": "different versions: 2.4.0 vs 2.4.1"}]}}, T0 + 4 * DAY)
    assert error["outcome"] == "error"
    question = db.execute("SELECT question FROM pending WHERE id=?", (error["detail"]["pending_id"],)).fetchone()[0]
    assert question.startswith("Accept as knowledge") and "\nUpdate: Prediction failed" in question
