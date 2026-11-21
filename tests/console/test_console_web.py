"""Owner console Web Cortex API (/api/web/*) over real HTTP, with the embedded research worker running offline:
FixtureWeb impersonates the public hosts and CorpusProvider stands in for the search engines."""
import threading
import time

import pytest

from neuroscifi.cli import build_parser
from neuroscifi.console import server
from neuroscifi.instance import store
from neuroscifi.webcortex import orchestrator, state
from neuroscifi.webcortex.fixtureweb import FixtureWeb
from neuroscifi.webcortex.providers.corpus import CorpusProvider
from test_console_api import TOKEN, Client

T0 = 1_800_000_000.0     # 2027-01-15
QUESTION = "What is the latest version of the Zephyr compiler?"
PAGES = {
    ("news-one.example", "/zephyr"): ("Zephyr compiler 4.2 released", "2027-01-12T10:00:00Z", [
        "The Zephyr compiler version 4.2.0 was released on 11 January 2027 by the Zephyr project team.",
        "The release adds faster incremental builds for large embedded projects."]),
    ("tech-two.example", "/news/zephyr-42"): ("Zephyr 4.2 is out", "2027-01-13T09:00:00Z", [
        "The Zephyr compiler version 4.2.0 was released on 11 January 2027 after a long beta.",
        "Developers report that the compiler now supports more targets."]),
    ("blog-three.example", "/zephyr-review"): ("Hands on with the Zephyr compiler", "2027-01-14T09:00:00Z", [
        "We tested the Zephyr compiler version 4.2.0 released on 11 January 2027 on three boards.",
        "Build times dropped by 30 percent in our tests."]),
}
SLOW = {   # okapi pages share no words with the Zephyr question and answer slowly (cancel tests)
    ("zoo-one.example", "/okapi"): ("Okapi in the wild", "2027-01-10T09:00:00Z", [
        "The okapi is a giraffid mammal native to the Ituri rainforest of the Congo basin.",
        "Okapi stripes help calves follow their mothers through dense forest."]),
    ("museum-two.example", "/okapi"): ("The forest giraffe", "2027-01-09T09:00:00Z", [
        "The okapi is a giraffid mammal native to the Ituri rainforest of the Congo basin.",
        "Its closest living relative is the giraffe."]),
    ("wild-three.example", "/okapi"): ("Okapi conservation", "2027-01-08T09:00:00Z", [
        "The okapi is a giraffid mammal native to the Ituri rainforest of the Congo basin.",
        "Poaching and habitat loss threaten okapi populations."]),
}
MISSING = "00000000-0000-4000-8000-000000000000"


def html(title, date, paragraphs):
    body = "".join(f"<p>{p}</p>" for p in paragraphs)
    return (f"<html><head><title>{title}</title><meta property='article:published_time' content='{date}'></head>"
            f"<body><article><h1>{title}</h1>{body}</article></body></html>")


def corpus():
    pages = {**PAGES, **SLOW}
    return CorpusProvider([{"url": f"http://{host}{path}", "title": title, "snippet": " ".join(paragraphs)[:160]}
                           for (host, path), (title, _, paragraphs) in pages.items()])


@pytest.fixture
def web():
    w = FixtureWeb().start()
    for (host, path), (title, date, paragraphs) in PAGES.items():
        w.add(host, path, html(title, date, paragraphs))
    for (host, path), page in SLOW.items():
        w.add(host, path, html(*page), delay=1.0)
    yield w
    w.stop()


def start_console(path, web, *, worker=True, clock=None):
    clock = clock or [T0 + 10]
    srv = server.ConsoleServer(path, 0, token=TOKEN, clock=lambda: clock[0], web_worker=worker,
                               worker_options={"fixture": web, "providers": [corpus()]})
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    client = Client(srv, path)
    client.clock = clock
    return client


def stop_console(client):
    client.srv.shutdown()
    client.srv.server_close()


@pytest.fixture
def instance(tmp_path):
    path = tmp_path / "individual.sqlite"
    db = store.create(path, T0)
    state.set_config(db, {"min_domain_interval": 0.0}, T0)   # FixtureWeb hosts need no politeness delay
    db.close()
    return path


@pytest.fixture
def console(instance, web):
    c = start_console(instance, web)
    yield c
    stop_console(c)


@pytest.fixture
def idle(instance, web):
    """A console without the embedded worker: tasks stay queued."""
    c = start_console(instance, web, worker=False)
    yield c
    stop_console(c)


def enable(c):
    assert c.ok("POST", "/api/web/config", {"enabled": True})["config"]["enabled"] is True


def wait_for(c, task_id, statuses=orchestrator.TERMINAL_STATES, timeout=60):
    deadline = time.monotonic() + timeout
    while True:
        task = c.ok("GET", f"/api/web/tasks/{task_id}")
        if task["status"] in statuses:
            return task
        assert time.monotonic() < deadline, task
        time.sleep(0.1)


def complete(c, objective=QUESTION, **extra):
    task = c.ok("POST", "/api/web/tasks", {"objective": objective, **extra})
    assert task["status"] == "queued" and task["origin"] == "console"
    return wait_for(c, task["id"])


# ---- security controls ---------------------------------------------------------------------------------

def test_web_routes_require_token_host_origin_and_small_bodies(console):
    c = console
    for route in ("/api/web/status", "/api/web/tasks", "/api/web/knowledge", "/api/web/monitors",
                  f"/api/web/tasks/{MISSING}"):
        assert c.get(route, token=None)[0] == 403
        assert c.get(route, token="wrong")[0] == 403
        assert c.call("GET", route, headers={"Host": f"evil.example:{c.port}"})[0] == 403
    assert c.post("/api/web/config", {"enabled": True}, token=None)[0] == 403
    status, _, _ = c.call("POST", "/api/web/tasks", {"objective": QUESTION},
                          headers={"Origin": "http://evil.example"})
    assert status == 403
    status, data = c.post("/api/web/tasks", {"objective": "x" * 9000})    # 8 KiB for questions (C-13)
    assert status == 413 and "8192" in data["error"]
    status, data = c.post("/api/web/config", {"enabled": True, "pad": "x" * 5000})
    assert status == 413 and "4096" in data["error"]
    status, _, data = c.call("POST", "/api/web/config", raw=b'{"enabled":true}', headers={"Content-Type": "text/plain"})
    assert status == 400 and "application/json" in data["error"]
    assert c.call("GET", "/api/web/tasks", raw=b"{}", headers={"Content-Type": "application/json"})[0] == 400
    status, headers, _ = c.call("GET", "/api/web/config")
    assert status == 405 and headers["Allow"] == "POST"
    assert c.call("DELETE", "/api/web/tasks")[0] == 405


def test_task_ids_are_strict_uuids(console):
    c = console
    for bad in ("not-a-uuid", "ABCDEF00-0000-4000-8000-000000000000", MISSING + "0", "%2e%2e", "1", "00000000-0000-4000-8000-00000000000g"):
        status, data = c.get(f"/api/web/tasks/{bad}")
        assert status == 400 and "UUID" in data["error"], bad
        assert c.post(f"/api/web/tasks/{bad}/cancel")[0] == 400
        assert c.post(f"/api/web/tasks/{bad}/resume")[0] == 400
        assert c.get(f"/api/web/tasks/{bad}/evidence")[0] == 400
    assert c.get(f"/api/web/tasks/{MISSING}")[0] == 404
    assert c.post(f"/api/web/tasks/{MISSING}/cancel")[0] == 404
    assert c.get("/api/web/tasks/a/b/c")[0] == 404
    assert c.post("/api/web/knowledge/9223372036854775808/review", {"accept": True})[0] == 404
    assert c.post("/api/web/knowledge/abc/review", {"accept": True})[0] == 404


# ---- configuration and status --------------------------------------------------------------------------

def test_disabled_by_default_and_owner_switches(console):
    c = console
    status = c.ok("GET", "/api/web/status")
    assert status["config"] == {**status["config"], "enabled": False, "autonomous": False, "pocket_research": False,
                                "browser_rendering": False}
    assert status["worker"] == {"embedded": True, "running": True, "note": None}
    assert status["curiosity"] is None and status["tasks"] == {} and status["active"] == 0
    assert "deep_research" in status["modes"] and len(status["modes"]) == 9
    assert {p["name"] for p in status["providers"]} >= {"openwebsearch", "wikipedia"}
    code, data = c.post("/api/web/tasks", {"objective": QUESTION})
    assert code == 409 and "disabled" in data["error"]
    code, data = c.post("/api/web/config", {"enabled": "yes"})
    assert code == 400 and "true or false" in data["error"]
    code, data = c.post("/api/web/config", {"min_domain_interval": 5})
    assert code == 400 and "min_domain_interval" in data["error"]       # not an owner switch in the console
    out = c.ok("POST", "/api/web/config", {"enabled": True, "autonomous": True, "browser_rendering": False})
    assert out["config"]["enabled"] and out["config"]["autonomous"] and not out["config"]["pocket_research"]
    assert c.ok("GET", "/api/web/status")["curiosity"] == []
    assert c.ok("POST", "/api/web/config", {"autonomous": False})["config"]["autonomous"] is False


def test_empty_config_change_is_rejected(console):
    status, data = console.post("/api/web/config", {})
    assert status == 400 and "non-empty" in data["error"]


# ---- research tasks ------------------------------------------------------------------------------------

def test_submit_runs_on_the_embedded_worker_and_reports(console, web):
    c = console
    enable(c)
    code, data = c.post("/api/web/tasks", {"objective": QUESTION, "mode": "warp"})
    assert code == 400 and "mode" in data["error"]
    assert c.post("/api/web/tasks", {"objective": QUESTION, "colour": "blue"})[0] == 400
    assert c.post("/api/web/tasks", {"objective": "   "})[0] == 400
    code, data = c.post("/api/web/tasks", {"objective": "Ask jo@example.org about the Zephyr compiler"})
    # T1-20: a privacy refusal is a normal outcome the page handles, not an HTTP error (no browser console error)
    assert code == 200 and data["kind"] == "refused" and data["kinds"] == ["email"] and "private data" in data["error"]
    assert c.ok("GET", "/api/web/tasks") == []
    done = complete(c, mode="auto")
    assert done["status"] == "completed", done["error"]
    assert done["stages_done"] == ["discovering", "reading", "evaluating", "synthesizing"]
    assert [p["stage"] for p in done["progress"] if p["kind"] == "stage"] == done["stages_done"]
    report = done["result"]
    assert report["live"] is True and len(report["sources"]) == 3
    quotes = [a for a in report["answer"] if a.get("type") == "quote"]
    assert quotes and "4.2.0" in quotes[0]["text"] and quotes[0]["citations"]
    assert done["usage"]["pages"] == 3 and done["budget"]["max_pages"] >= 3
    listed = c.ok("GET", "/api/web/tasks")
    assert [t["id"] for t in listed] == [done["id"]] and "result" not in listed[0]
    assert c.ok("GET", "/api/web/tasks?status=completed&limit=5")[0]["id"] == done["id"]
    assert c.ok("GET", "/api/web/tasks?status=queued") == []
    assert c.get("/api/web/tasks?status=bogus")[0] == 400
    assert c.get("/api/web/tasks?limit=0")[0] == 400
    assert c.get("/api/web/tasks?colour=red")[0] == 400
    shown = c.ok("GET", f"/api/web/tasks/{done['id']}/evidence")
    assert len(shown["sources"]) == 3 and shown["claims"] and all(x["untrusted"] for x in shown["claims"])
    status = c.ok("GET", "/api/web/status")
    assert status["tasks"] == {"completed": 1} and status["active"] == 0
    assert status["cache"]["documents"] >= 3 and "path" not in status["cache"]
    assert any(p["name"] == "corpus" and p["successes"] >= 1 for p in status["providers"])
    assert isinstance(status["curiosity"], list)


def test_cancel_a_running_task(console, web):
    c = console
    enable(c)
    task = c.ok("POST", "/api/web/tasks", {"objective": "Where does the okapi live?", "mode": "fact_verification"})
    wait_for(c, task["id"], ("reading",), timeout=30)
    cancelled = c.ok("POST", f"/api/web/tasks/{task['id']}/cancel", {})
    assert cancelled["cancel_requested"] is True
    done = wait_for(c, task["id"])
    assert done["status"] == "cancelled"
    assert any("cancelled" in item for item in done["result"]["limitations"])
    assert c.ok("POST", f"/api/web/tasks/{task['id']}/cancel", {})["status"] == "cancelled"   # idempotent
    assert c.post(f"/api/web/tasks/{task['id']}/cancel", {"now": True})[0] == 400


def test_cancel_queued_and_resume_as_a_linked_task(idle):
    c = idle
    enable(c)
    task = c.ok("POST", "/api/web/tasks", {"objective": QUESTION, "mode": "fact_verification"})
    code, data = c.post(f"/api/web/tasks/{task['id']}/resume")
    assert code == 409 and "queued" in data["error"]
    assert c.ok("POST", f"/api/web/tasks/{task['id']}/cancel", {})["status"] == "cancelled"
    again = c.ok("POST", f"/api/web/tasks/{task['id']}/resume", {})
    assert again["id"] != task["id"] and again["parent_id"] == task["id"] and again["resumed_from"] == task["id"]
    assert again["status"] == "queued" and again["mode"] == "fact_verification" and again["objective"] == QUESTION


def test_resume_a_failed_task_requeues_it_and_the_worker_finishes(instance, web):
    c = start_console(instance, web, worker=False)
    try:
        enable(c)
        task = c.ok("POST", "/api/web/tasks", {"objective": QUESTION, "mode": "fact_verification"})
        db = store.open_existing(instance)
        with store.write(db):
            state.update_task(db, task["id"], T0, status="failed", error="RuntimeError: simulated", finished_at=T0)
        db.close()
        again = c.ok("POST", f"/api/web/tasks/{task['id']}/resume", {})
        assert again["id"] == task["id"] and again["status"] == "queued" and again["error"] is None
    finally:
        stop_console(c)
    c = start_console(instance, web)
    try:
        done = wait_for(c, task["id"])
        assert done["status"] == "completed", done["error"]
        assert "resumed" in [p["kind"] for p in done["progress"]]
    finally:
        stop_console(c)


def test_two_workers_never_run_one_task_twice(instance, web):
    first, second = start_console(instance, web), start_console(instance, web)
    try:
        enable(first)
        ids = [first.ok("POST", "/api/web/tasks", {"objective": QUESTION, "mode": "quick_lookup"})["id"]
               for _ in range(3)]
        for task_id in ids:
            assert wait_for(first, task_id)["status"] == "completed"
    finally:
        stop_console(first)
        stop_console(second)
    db = store.open_existing(instance)
    try:
        for task_id in ids:
            assert [e["kind"] for e in state.events(db, task_id)].count("started") == 1
    finally:
        db.close()


# ---- knowledge and monitors ----------------------------------------------------------------------------

def test_knowledge_review_archive_refresh_and_forget(console):
    c = console
    enable(c)
    assert c.ok("GET", "/api/web/knowledge")["items"] == []
    done = complete(c, mode="fact_verification")
    provisional = done["result"]["consolidation"]["provisional"]
    assert provisional, done["result"]["consolidation"]
    kid = provisional[0]
    recent = c.ok("GET", "/api/web/knowledge")["items"]
    item = next(i for i in recent if i["id"] == kid)
    assert item["status"] == "provisional" and item["sources"] and item["versions"]
    assert isinstance(item["feedback"], list)
    found = c.ok("GET", "/api/web/knowledge?topic=Zephyr%20compiler%20version")
    assert kid in [i["id"] for i in found["items"]]
    assert c.get("/api/web/knowledge?topic=" + "x" * 2100)[0] == 400
    assert c.post(f"/api/web/knowledge/{kid}/review", {"accept": "yes"})[0] == 400
    assert c.post(f"/api/web/knowledge/{kid}/review", {"accept": True, "note": 5})[0] == 400
    reviewed = c.ok("POST", f"/api/web/knowledge/{kid}/review", {"accept": True, "note": "checked the release notes"})
    assert reviewed["status"] == "accepted" and reviewed["previous_status"] == "provisional"
    refreshed = c.ok("POST", f"/api/web/knowledge/{kid}/refresh", {})
    # the owner's refresh (C-08: L2 records it as refresh-owner:<id>, so forgetting the item finds it)
    assert refreshed["origin"] in ("console", f"refresh-owner:{kid}") and refreshed["mode"] == "fact_verification"
    wait_for(c, refreshed["id"])
    assert c.ok("POST", f"/api/web/knowledge/{kid}/archive", {"note": "kept for history"})["status"] == "archived"
    code, data = c.post(f"/api/web/knowledge/{kid}/refresh", {})
    assert code == 409
    assert c.ok("POST", f"/api/web/knowledge/{kid}/forget", {})["status"] == "forgotten"
    assert c.post(f"/api/web/knowledge/{kid}/archive", {})[0] == 404
    assert c.ok("POST", f"/api/web/knowledge/{kid}/forget", {})["status"] == "absent"
    assert c.post("/api/web/knowledge/999999/review", {"accept": False})[0] == 404


def test_monitor_lifecycle(console):
    c = console
    assert c.ok("GET", "/api/web/monitors") == []
    enable(c)
    for bad in ({"topic": "Zephyr compiler releases", "interval_hours": 0.5},
                {"topic": "Zephyr compiler releases", "interval_hours": True},
                {"topic": "", "interval_hours": 24},
                {"topic": "Zephyr compiler releases", "interval_hours": 24, "mode": "warp"},
                {"topic": "Zephyr compiler releases", "interval_hours": 24, "authorized_by": "owner"}):
        assert c.post("/api/web/monitors", bad)[0] == 400, bad
    mon = c.ok("POST", "/api/web/monitors", {"topic": "Zephyr compiler releases", "interval_hours": 24})
    assert mon["status"] == "active" and mon["authorized_by"] == "owner" and mon["interval_seconds"] == 86400
    assert mon["mode"] == "continuous"
    assert c.ok("POST", f"/api/web/monitors/{mon['id']}/pause", {})["status"] == "paused"
    assert c.ok("POST", f"/api/web/monitors/{mon['id']}/resume", {})["status"] == "active"
    assert c.ok("POST", f"/api/web/monitors/{mon['id']}/stop", {})["status"] == "stopped"
    assert c.post(f"/api/web/monitors/{mon['id']}/resume", {})[0] == 409
    assert c.post("/api/web/monitors/424242/pause", {})[0] == 404
    assert [m["status"] for m in c.ok("GET", "/api/web/monitors")] == ["stopped"]


# ---- worker lifecycle ------------------------------------------------------------------------------------

def test_web_worker_is_opt_in_and_shutdown_stops_the_worker(instance, web):
    """C-P0-1: the embedded worker is opt-in (`--web-worker`); `--no-web-worker` is still accepted (the default)."""
    parse = lambda *extra: build_parser().parse_args(["console", "--db", str(instance), *extra])  # noqa: E731
    assert parse().web_worker is False and parse("--web-worker").web_worker is True
    assert parse("--no-web-worker").web_worker is False
    with pytest.raises(SystemExit):
        parse("--web-worker", "--no-web-worker")
    default = server.ConsoleServer(instance, 0, token=TOKEN)
    try:
        assert default.worker is None
    finally:
        default.server_close()
    c = start_console(instance, web)
    worker = c.srv.worker
    assert worker.is_alive()
    stop_console(c)
    assert not worker.is_alive()
    c = start_console(instance, web, worker=False)
    try:
        status = c.ok("GET", "/api/web/status")["worker"]
        assert c.srv.worker is None and status["embedded"] is False and status["running"] is False
        assert "neuroscifi-webcortex" in status["note"] and "--web-worker" in status["note"]
    finally:
        stop_console(c)


# ---- review remediation V2 ---------------------------------------------------------------------------------------

def test_monitors_api_shows_recent_findings(instance, web):
    """A-P2-7: the console's monitors list carries each monitor's recent findings (newest first)."""
    c = start_console(instance, web, worker=False)
    try:
        c.ok("POST", "/api/web/config", {"enabled": True})
        mon = c.ok("POST", "/api/web/monitors", {"topic": "Zephyr compiler releases", "interval_hours": 24})
        assert c.ok("GET", "/api/web/monitors")[0]["findings"] == []
        db = store.connect(instance)
        try:
            with store.write(db):
                state.log_event(db, None, "finding", {"monitor_id": mon["id"], "changes": {
                    "meaningful": True, "changed_values": [{"key": "zephyr version", "before": ["v4.2.0"],
                                                            "after": ["v4.3.0"], "polarity": [1, 1]}],
                    "new_claims": [], "superseded": [], "pending_id": 7}}, T0 + 5)
        finally:
            db.close()
        found = c.ok("GET", "/api/web/monitors")[0]["findings"]
        assert len(found) == 1 and found[0]["pending_id"] == 7
        assert found[0]["changed_values"][0]["after"] == ["v4.3.0"]
    finally:
        stop_console(c)


def test_embedded_worker_errors_are_logged_and_backed_off(instance, monkeypatch, capsys):
    """C-P2-4: exception type and a truncated message are logged; the retry delay doubles up to 60 s."""
    from neuroscifi.webcortex import scheduler
    srv = server.ConsoleServer(instance, 0, token=TOKEN)
    waits = []

    def boom(*args, **kwargs):
        raise RuntimeError("database disk image is malformed " + "x" * 500)

    def wait(seconds):
        waits.append(seconds)
        if len(waits) >= 6:
            srv.worker_stop.set()
        return srv.worker_stop.is_set()

    monkeypatch.setattr(scheduler, "work", boom)
    monkeypatch.setattr(srv.worker_stop, "wait", wait)
    try:
        srv._work()
    finally:
        srv.server_close()
    assert waits == [5.0, 10.0, 20.0, 40.0, 60.0, 60.0]
    err = capsys.readouterr().err
    assert "RuntimeError: database disk image is malformed" in err and "x" * 300 not in err


# ---- UAT T1: questions the Research view answers without a new web search -------------------------------------

def test_a_question_about_the_individuals_own_understanding_is_answered_from_its_history(idle):
    """T1-04: "What has changed in your understanding of predictive coding?" ran a new web search (change_detection)
    and never mentioned the stored 1998 -> 1999 revision. It is answered from evolution.changes; a fresh web run is a
    separate action (route 'research')."""
    from neuroscifi.webcortex import knowledge
    c = idle
    enable(c)
    db = store.open_existing(c.path)
    knowledge.assert_fact(db, "Rao and Ballard published the predictive coding model of the visual cortex in 1998.",
                          T0)
    knowledge.assert_fact(db, "Rao and Ballard published the predictive coding model of the visual cortex in 1999.",
                          T0 + 5)
    db.close()
    question = "What has changed in your understanding of predictive coding?"
    out = c.ok("POST", "/api/web/tasks", {"objective": question, "mode": "auto"})
    assert out["kind"] == "history" and out["topic"] == "predictive coding"
    subject = next(s for s in out["history"]["subjects"] if s["versions"])
    assert [(v["version"], v["status"]) for v in subject["versions"]] == [(1, "superseded"), (2, "accepted")]
    assert "1998" in subject["versions"][0]["statement"] and subject["versions"][0]["superseded_by"]
    assert any(e["kind"] == "superseded" for e in subject["timeline"]) and subject["changed"] is True
    assert c.ok("GET", "/api/web/tasks") == []                                    # nothing was searched
    assert out["research"] == {"objective": question, "route": "research"}
    task = c.ok("POST", "/api/web/tasks", out["research"])                        # the explicit fresh run
    assert task["status"] == "queued" and task["mode"] == "change_detection"


def test_a_follow_up_is_read_about_the_previous_question_of_the_session(idle):
    """T1-10: "Is that version secure, stable and recommended?" after the numpy questions was researched about Go 1.
    The page sends its session's previous question; the server resolves the reference or asks."""
    c = idle
    enable(c)
    question = "Is that version secure, stable and recommended?"
    out = c.ok("POST", "/api/web/tasks", {"objective": question})
    assert out["kind"] == "clarify" and out["clarify"].startswith("Which version do you mean?")
    assert c.ok("GET", "/api/web/tasks") == []
    task = c.ok("POST", "/api/web/tasks", {"objective": question, "previous": "Is numpy 2.5.5 recommended?"})
    assert task["objective"] == "Is numpy 2.5.5 secure, stable and recommended?"
    assert task["resolved_followup"]["subject"] == "numpy 2.5.5"
    assert task["resolved_followup"]["anaphor"] == "that version"
    assert c.post("/api/web/tasks", {"objective": question, "previous": 7})[0] == 400
    assert c.post("/api/web/tasks", {"objective": question, "route": "elsewhere"})[0] == 400
