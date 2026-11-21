"""Monitors, the timer tick (fake clock, offline fixture web) and the worker's lease handling."""
import json
import threading

import pytest

from neuroscifi.instance import store
from neuroscifi.instance.errors import Conflict, ValidationError
from neuroscifi.webcortex import orchestrator, predictions, scheduler, services as svc_mod, state
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


# ---- remediation regressions (RB2) -----------------------------------------------------------------------------

def snap(**claims):
    return {"urls": {f"http://x.example/{k}": k for k in claims}, "claims": claims}


def info(values, *, status="single_source", primary=False, groups=1, relevance=0.8, polarity=1):
    return {"values": values, "polarity": polarity, "status": status, "primary": primary, "groups": groups,
            "relevance": relevance}


def test_compare_ignores_search_churn_and_reports_value_changes():
    """F6: a new single-source API record that is barely relevant, a key seen in any earlier run, or new URLs are
    not change; a new value for a known key, or a new corroborated key, is."""
    gold = info(["v4.2.0"], status="corroborated", groups=3)
    tick1 = snap(**{"zephyr compiler version": gold, "zephyr paper alpha": info([], primary=True, relevance=0.3)})
    baseline = scheduler.merge_baseline({}, tick1)
    tick2 = snap(**{"zephyr compiler version": gold, "zephyr paper beta": info([], primary=True, relevance=0.3)})
    churn = scheduler.compare(baseline, tick2)
    assert churn["meaningful"] is False and churn["new_urls"] and churn["new_claims"] == []
    baseline = scheduler.merge_baseline(baseline, tick2)
    tick3 = snap(**{"zephyr compiler version": gold, "zephyr paper alpha": info([], status="corroborated", groups=2)})
    assert scheduler.compare(baseline, tick3)["meaningful"] is False     # alpha was seen two runs ago
    tick4 = snap(**{"zephyr compiler version": gold, "compiler version zephyr": info(["x"], status="corroborated",
                                                                                     groups=2)})
    assert scheduler.compare(baseline, tick4)["meaningful"] is False     # same subject, reworded key
    changed = scheduler.compare(baseline, snap(**{"zephyr compiler version": info(["v4.3.0"], status="corroborated",
                                                                                  groups=3)}))
    assert changed["meaningful"] and changed["changed_values"][0]["after"] == ["v4.3.0"]
    new = scheduler.compare(baseline, snap(**{"zephyr security advisory": info(["CVE-2026-1"], primary=True,
                                                                               relevance=0.9)}))
    assert new["meaningful"] and new["new_claims"] == ["zephyr security advisory"]
    bounded = baseline
    for i in range(scheduler.MAX_SEEN_KEYS + 10):
        bounded = {**bounded, "seen_keys": bounded["seen_keys"] + [f"k{i}"]}
    assert len(scheduler.merge_baseline(bounded, tick1)["seen_keys"]) == scheduler.MAX_SEEN_KEYS


def test_two_ticks_over_equivalent_result_mixes_report_no_change(individual, site):
    """F6 end to end: the second tick sees a different mix (another page, another weakly relevant scholarly
    record) saying the same thing -> no finding; a version change on the pages -> a finding."""
    from neuroscifi.webcortex.providers.corpus import CorpusProvider
    from test_orchestrator import ListProvider
    db, path = individual
    mon = scheduler.monitor_add(db, TOPIC, T0, interval=6 * HOUR, authorized_by="owner")
    pages = [{"url": f"http://{h}{p}", "title": t, "snippet": " ".join(ps)[:160]}
             for (h, p), (t, _, ps) in PAGES.items()]

    def record(i, title):
        return {"url": f"https://openalex.org/W{i}", "title": title, "snippet": "", "kind": "academic",
                "metadata": {"doi": f"10.1000/{i}", "title": title,
                             "abstract": f"{title}. We describe embedded toolchains for sensor boards."}}

    def tick(now, web_pages, rec):
        services = svc_mod.build(db, path, fixture=site, clock=lambda: now,
                                 providers=[CorpusProvider(web_pages), ListProvider([rec], name="openalex")])
        try:
            return scheduler.tick(db, now, services)
        finally:
            services.close()

    first = tick(T0, pages[:2], record(1, "Embedded toolchains for the Zephyr ecosystem"))
    assert first["findings"] == [] and first["ran"][0]["status"] in ("completed", "partial")
    second = tick(T0 + 6 * HOUR, pages[1:], record(2, "Sensor board firmware with the Zephyr toolchain"))
    assert second["ran"] and second["findings"] == [], json.dumps(second["findings"][0]["changes"])[:3000]
    republish(site, "4.3.0", 21)
    third = tick(T0 + 12 * HOUR, pages, record(2, "Sensor board firmware with the Zephyr toolchain"))
    assert len(third["findings"]) == 1 and third["findings"][0]["monitor_id"] == mon["id"]
    assert len(scheduler.monitor_list(db)[0]["baseline"]["seen_keys"]) >= 2


def test_autonomous_budgets_are_clamped_to_the_daily_allowance(individual, site):
    """Autonomous tasks get at most the requests/bytes left of the daily cap (open tasks count with their whole
    budget), so the cap is passed by at most one request; nothing left -> not submitted."""
    db, path = individual
    state.set_config(db, {"daily_autonomous": {"tasks": 10, "requests": 15, "bytes": 10_000_000}}, T0)
    first = orchestrator.submit(db, "Zephyr compiler latest version", T0, mode="fact_verification",
                                origin="curiosity")
    assert first["budget"]["max_requests"] == 15 and first["budget"]["max_bytes"] == 10_000_000
    assert scheduler.autonomous_remaining(db, T0, state.get_config(db))["requests"] == 0   # reserved while queued
    with pytest.raises(ValidationError, match="daily autonomous cap reached: requests"):
        orchestrator.submit(db, "Zephyr compiler targets", T0, mode="fact_verification", origin="refresh:1")
    owner = orchestrator.submit(db, "Zephyr compiler targets", T0, mode="fact_verification")   # not autonomous
    assert owner["budget"]["max_requests"] == 40
    services = build(db, path, site)
    try:
        done = orchestrator.run(db, first["id"], services)
    finally:
        services.close()
    used = scheduler.autonomous_usage(db, T0)
    assert done["usage"]["requests"] <= 15 and used["requests"] <= 15
    left = scheduler.autonomous_remaining(db, T0, state.get_config(db))["requests"]
    assert left == 15 - used["requests"]
    if left:
        second = orchestrator.submit(db, "Zephyr compiler targets", T0, mode="fact_verification", origin="curiosity")
        assert second["budget"]["max_requests"] == left
    proposal = orchestrator.submit(db, "Zephyr compiler boards", T0, mode="fact_verification", origin="curiosity",
                                   proposed=True)        # the owner authorises proposals; not clamped at proposal
    assert proposal["budget"]["max_requests"] == 40


def test_tick_and_runs_apply_cache_retention(individual, site, monkeypatch):
    """R8: the timer tick and the end of every run call PublicCache.maybe_prune with the configured limits."""
    from neuroscifi.webcortex.cache import PublicCache
    db, path = individual
    state.set_config(db, {"cache_max_bytes": 4096, "cache_max_age_days": 2.0}, T0)
    calls = []
    real = PublicCache.maybe_prune

    def spy(self, now, **kw):
        calls.append((now, kw))
        return real(self, now, **kw)

    monkeypatch.setattr(PublicCache, "maybe_prune", spy)
    task = orchestrator.submit(db, "What is the latest version of the Zephyr compiler?", T0, mode="quick_lookup")
    services = build(db, path, site)
    try:
        orchestrator.run(db, task["id"], services)
        report = scheduler.tick(db, T0 + 10, services)
    finally:
        services.close()
    assert len(calls) == 2 and all(kw == {"max_bytes": 4096, "max_age_seconds": 2.0 * DAY} for _, kw in calls)
    assert "cache" in report
