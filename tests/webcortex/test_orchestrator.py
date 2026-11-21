"""Orchestrator end to end on the REAL acquisition, discovery, evidence and synthesis modules, offline: FixtureWeb
impersonates the public hosts and providers.corpus.CorpusProvider stands in for the search engines."""
import pytest

from neuroscifi.instance import evidence, store
from neuroscifi.instance.errors import Conflict, ValidationError
from neuroscifi.webcortex import knowledge, orchestrator, services as svc_mod, state
from neuroscifi.webcortex.providers.corpus import CorpusProvider
from test_consolidate import claim, cluster, consolidated

T0 = 1_795_200_000.0            # 2026-11-21
QUESTION = "What is the latest version of the Zephyr compiler?"
PAGES = {
    ("news-one.example", "/zephyr"): ("Zephyr compiler 4.2 released", "2026-11-18T10:00:00Z", [
        "The Zephyr compiler version 4.2.0 was released on 17 November 2026 by the Zephyr project team.",
        "The release adds faster incremental builds for large embedded projects."]),
    ("tech-two.example", "/news/zephyr-42"): ("Zephyr 4.2 is out", "2026-11-19T09:00:00Z", [
        "The Zephyr compiler version 4.2.0 was released on 17 November 2026 after a long beta.",
        "Developers report that the compiler now supports more targets."]),
    ("blog-three.example", "/zephyr-review"): ("Hands on with the Zephyr compiler", "2026-11-20T09:00:00Z", [
        "We tested the Zephyr compiler version 4.2.0 released on 17 November 2026 on three boards.",
        "Build times dropped by 30 percent in our tests."]),
}


def html(title, date, paragraphs):
    body = "".join(f"<p>{p}</p>" for p in paragraphs)
    return (f"<html><head><title>{title}</title><meta property='article:published_time' content='{date}'></head>"
            f"<body><article><h1>{title}</h1>{body}</article></body></html>")


def serve(web):
    for (host, path), (title, date, paragraphs) in PAGES.items():
        web.add(host, path, html(title, date, paragraphs))
    return web


@pytest.fixture
def site(web):
    return serve(web)


def corpus():
    return CorpusProvider([{"url": f"http://{host}{path}", "title": title, "snippet": " ".join(paragraphs)[:160]}
                           for (host, path), (title, _, paragraphs) in PAGES.items()])


def open_individual(tmp_path):
    path = tmp_path / "individual.sqlite"
    db = store.create(path, T0)
    state.set_config(db, {"enabled": True, "min_domain_interval": 0.0}, T0)
    try:
        yield db, path
    finally:
        db.close()


@pytest.fixture
def individual(tmp_path):
    yield from open_individual(tmp_path)


def build(db, path, web, clock=None):
    return svc_mod.build(db, path, fixture=web, providers=[corpus()], clock=clock or (lambda: T0))


def page_hits(web):
    return [r for r in web.log if r["path"] != "/robots.txt"]


def test_completed_report_cites_live_sources_and_consolidates(individual, site):
    db, path = individual
    task = orchestrator.submit(db, QUESTION, T0)
    assert task["status"] == "queued" and task["mode"] in ("technical", "quick_lookup", "fact_verification")
    services = build(db, path, site)
    try:
        done = orchestrator.run(db, task["id"], services)
    finally:
        services.close()
    assert done["status"] == "completed", done["error"]
    report = done["result"]
    assert report["live"] is True and len(report["sources"]) == 3
    assert all(s["retrieval_mode"] == "live" for s in report["sources"])
    quotes = [a for a in report["answer"] if a.get("type") == "quote"]
    assert quotes and quotes[0]["citations"] and "4.2.0" in quotes[0]["text"]
    assert {n for a in report["answer"] for n in a["citations"]} <= {s["n"] for s in report["sources"]}
    stages = [e["detail"]["stage"] for e in state.events(db, task["id"]) if e["kind"] == "stage"]
    assert stages == ["discovering", "reading", "evaluating", "synthesizing"]
    assert done["stages_done"] == ["discovering", "reading", "evaluating", "synthesizing"]
    assert done["usage"]["pages"] == 3 and done["executed_on"]["cpus"] and len(done["executed_on"]["host"]) == 12
    assert len(state.sources(db, task["id"])) == 3 and state.claims(db, task["id"])
    assert report["consolidation"]["provisional"], report["consolidation"]
    kid = report["consolidation"]["provisional"][0]
    assert db.execute("SELECT count(*) FROM web_predictions WHERE knowledge_id=?", (kid,)).fetchone()[0] == 1
    assert db.execute("SELECT count(*) FROM pending WHERE scope=?", (f"web:knowledge:{kid}",)).fetchone()[0] == 1
    assert state.load_strategies(db)["health"]["corpus"]
    assert state.load_strategies(db, f"mode:{done['mode']}")["completed"] == 1
    shown = orchestrator.evidence(db, task["id"])
    assert len(shown["sources"]) == 3 and all(c["untrusted"] for c in shown["claims"])
    assert orchestrator.listing(db)[0]["id"] == task["id"] and orchestrator.run(db, task["id"], services)["status"] == \
        "completed"   # a finished task is not run again


class CancellingFetcher:
    """Delegates to the real SafeFetcher; after the first page another connection sets the cancel flag."""

    def __init__(self, inner, path, task_id):
        self.inner, self.path, self.task_id, self.calls = inner, path, task_id, 0

    def fetch(self, url, **kwargs):
        result = self.inner.fetch(url, **kwargs)
        self.calls += 1
        if self.calls == 1:
            other = store.connect(self.path)
            try:
                orchestrator.cancel(other, self.task_id, T0 + 1)
            finally:
                other.close()
        return result

    def __getattr__(self, name):
        return getattr(self.inner, name)


def test_cancel_from_another_connection_stops_before_the_next_fetch(individual, site):
    db, path = individual
    task = orchestrator.submit(db, QUESTION, T0, mode="fact_verification")
    services = build(db, path, site)
    with store.write(db):   # the run holds the lease, so cancel() requests instead of cancelling outright
        state.update_task(db, task["id"], T0, lease_holder=services.holder, lease_expires_at=T0 + 600)
    services.fetcher = CancellingFetcher(services.fetcher, path, task["id"])
    try:
        done = orchestrator.run(db, task["id"], services)
    finally:
        services.close()
    assert done["status"] == "cancelled" and done["cancel_requested"] is True
    assert len(page_hits(site)) == 1
    report = done["result"]
    assert report is not None and any("cancelled" in item for item in report["limitations"])
    kinds = [e["kind"] for e in state.events(db, task["id"])]
    assert "cancel_requested" in kinds and kinds[-1] == "cancelled"
    assert db.execute("SELECT count(*) FROM web_knowledge").fetchone()[0] == 0   # nothing consolidated


def test_cancel_of_a_queued_task_is_immediate(individual):
    db, _ = individual
    task = orchestrator.submit(db, QUESTION, T0, mode="quick_lookup")
    assert orchestrator.cancel(db, task["id"], T0 + 1)["status"] == "cancelled"
    with pytest.raises(Conflict):
        orchestrator.resume(db, task["id"], None)


class Crash(BaseException):
    """Stands in for the process dying (not an Exception, so nothing in the run catches it)."""


def test_resume_after_a_crash_at_reading_uses_cached_documents(individual, site, monkeypatch):
    db, path = individual
    task = orchestrator.submit(db, QUESTION, T0, mode="fact_verification")
    real_done = orchestrator._Run.done

    def crash_after_reading(self, name, **updates):
        if name == "reading":
            raise Crash()
        return real_done(self, name, **updates)

    monkeypatch.setattr(orchestrator._Run, "done", crash_after_reading)
    services = build(db, path, site)
    with pytest.raises(Crash):
        orchestrator.run(db, task["id"], services)
    services.close()
    crashed = state.task(db, task["id"])
    assert crashed["status"] == "reading" and len(crashed["checkpoint"]["documents"]) == 3
    hits = len(page_hits(site))
    assert hits == 3
    monkeypatch.setattr(orchestrator._Run, "done", real_done)

    later = build(db, path, site, clock=lambda: T0 + 60)
    try:
        done = orchestrator.resume(db, task["id"], later)
    finally:
        later.close()
    assert done["status"] == "completed", done["error"]
    assert len(page_hits(site)) == hits      # nothing fetched again
    report = done["result"]
    assert report["live"] is False and all(s["retrieval_mode"] == "cache" for s in report["sources"])
    assert {s["retrieval_mode"] for s in state.sources(db, task["id"])} == {"cache"}
    assert "resumed" in [e["kind"] for e in state.events(db, task["id"])]


def test_page_budget_allows_exactly_one_fetch(individual, site):
    db, path = individual
    task = orchestrator.submit(db, QUESTION, T0, mode="fact_verification", budget={"max_pages": 1})
    assert task["budget"]["max_pages"] == 1
    services = build(db, path, site)
    try:
        done = orchestrator.run(db, task["id"], services)
    finally:
        services.close()
    assert len(page_hits(site)) == 1 and done["usage"]["pages"] == 1
    assert done["status"] in ("completed", "partial") and len(done["result"]["sources"]) == 1
    with pytest.raises(ValidationError):
        orchestrator.submit(db, QUESTION, T0, budget={"max_pagez": 1})


def test_fresh_accepted_knowledge_answers_without_the_web(individual, web):
    db, path = individual
    statement = "The okapi is a giraffid mammal native to the Ituri rainforest of the Democratic Republic of the Congo."
    clusters = [cluster([claim(f"http://{h}/okapi", statement, key="okapi giraffid mammal native")
                         for h in ("zoo.example", "museum.example")])]
    _, out = consolidated(db, clusters, T0 - 3600, objective="What is an okapi?")
    knowledge.review(db, out["provisional"][0], True, T0 - 3000)
    task = orchestrator.submit(db, "Where is the okapi native to?", T0)
    services = build(db, path, web)
    try:
        done = orchestrator.run(db, task["id"], services)
    finally:
        services.close()
    assert done["status"] == "completed" and web.log == []
    report = done["result"]
    assert report["live"] is False and report["memory"]["answered_from_memory"] is True
    assert report["memory"]["knowledge_ids"] == out["provisional"]
    assert any("Ituri" in a["text"] for a in report["answer"])
    decision = next(e for e in state.events(db, task["id"]) if e["kind"] == "decision")
    assert decision["detail"]["needs_web"] is False and decision["detail"]["reasons"]


def test_privacy_screen_blocks_private_identifiers(individual):
    db, _ = individual
    evidence.start_epoch(db, "bench", T0)
    evidence.observe(db, {"id": "w1", "epoch": "bench", "channel": "word", "action": 0, "bit": 1,
                          "root": "Alice-zq9"}, T0)
    with pytest.raises(ValidationError) as caught:
        orchestrator.submit(db, "What does Alice-zq9 think about jo@example.org lately?", T0)
    message = str(caught.value)
    assert "email" in message and "private_term" in message and "Alice-zq9" not in message
    assert db.execute("SELECT count(*) FROM web_tasks").fetchone()[0] == 0
    ok = orchestrator.submit(db, "What does Alice-zq9 think?", T0, mode="quick_lookup",
                             authorization=orchestrator.AUTHORIZED_DISCLOSURE)
    assert ok["status"] == "queued"
    state.set_config(db, {"enabled": False}, T0)
    with pytest.raises(Conflict):
        orchestrator.submit(db, QUESTION, T0)
