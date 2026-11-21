"""Pocket research (ADR-028) over a real in-process HTTP bridge and a temporary individual.

Scenario F end to end: a phone queues a task, the Spark's worker runs it offline (FixtureWeb + CorpusProvider), a
linked tablet reads the report and can cancel, an unrelated pairing sees nothing, and the owner sees everything.
"""
from contextlib import closing
from http.client import HTTPConnection
import json
import threading
import time

import pytest

from neuroscifi.instance import store
from neuroscifi.pocket import server as bridge
from neuroscifi.webcortex import orchestrator, scheduler, state
from neuroscifi.webcortex.fixtureweb import FixtureWeb
from neuroscifi.webcortex.providers.corpus import CorpusProvider

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


def corpus():
    return CorpusProvider([{"url": f"http://{host}{path}", "title": title, "snippet": " ".join(paragraphs)[:160]}
                           for (host, path), (title, _, paragraphs) in PAGES.items()])


@pytest.fixture
def web():
    w = FixtureWeb().start()
    for (host, path), (title, date, paragraphs) in PAGES.items():
        w.add(host, path, html(title, date, paragraphs))
    try:
        yield w
    finally:
        w.stop()


class Spark:
    """The bridge on a free port, its own Pocket DB, and an individual with Pocket research switched on."""

    def __init__(self, root):
        self.instance = root / "individual.sqlite"
        with closing(store.create(self.instance, time.time())) as db:
            state.set_config(db, {"enabled": True, "pocket_research": True, "min_domain_interval": 0.0}, time.time())
        self.server = bridge.PocketServer(("127.0.0.1", 0), bridge.Handler)
        self.server.store = bridge.Store(root / "mobile-private.sqlite")
        self.server.pair_lock = threading.Lock()
        self.server.invitation, self.server.invitation_deadline = "invite", time.monotonic() + 60
        self.server.instance_db = str(self.instance)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.host = f"127.0.0.1:{self.server.server_port}"

    def req(self, route, data=None, token="", origin=None, raw=None):
        c = HTTPConnection("127.0.0.1", self.server.server_port, timeout=10)
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = "Bearer " + token
        if origin:
            headers["Origin"] = origin
        c.request("POST", "/api/" + route, raw if raw is not None else json.dumps({} if data is None else data),
                  headers)
        resp = c.getresponse()
        status, body = resp.status, resp.read()
        c.close()
        return status, json.loads(body) if body else {}

    def configure(self, **changes):
        with closing(store.connect(self.instance)) as db:
            state.set_config(db, changes, time.time())

    def owner(self):
        return closing(store.connect(self.instance))

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def spark(tmp_path):
    s = Spark(tmp_path)
    yield s
    s.close()


@pytest.fixture
def devices(spark):
    """Phone A paired by invitation, tablet B linked by A's handoff code, unrelated C paired separately."""
    status, a = spark.req("pair", {"code": "invite"})
    assert status == 201
    status, link = spark.req("link", {}, a["key"])
    assert status == 201
    status, b = spark.req("pair", {"code": link["code"]})
    assert status == 201
    c = spark.server.store.pair()
    return a["key"], b["key"], c


def submit(spark, token, objective=QUESTION, **extra):
    return spark.req("research_submit", {"objective": objective, **extra}, token)


def test_feature_is_invisible_when_off(spark, devices):
    a, _, _ = devices
    on = {"research_submit": {"objective": QUESTION}, "research_list": {}, "research_get": {"id": "x"},
          "research_cancel": {"id": "x"}}
    assert spark.req("state", {}, a)[1]["research"] is True
    for changes in ({"pocket_research": False}, {"pocket_research": True, "enabled": False}):
        spark.configure(**changes)
        for route, body in on.items():
            assert spark.req(route, body, a)[0] == 404, (changes, route)
            assert spark.req(route, body)[0] == 404, "no hint before authentication either"
        assert "research" not in spark.req("state", {}, a)[1]
    spark.configure(enabled=True, pocket_research=True)
    spark.server.instance_db = None   # bridge started without --instance-db
    for route, body in on.items():
        assert spark.req(route, body, a)[0] == 404
    assert "research" not in spark.req("state", {}, a)[1]


def test_submit_list_get_cancel(spark, devices):
    a, _, _ = devices
    status, body = submit(spark, a, mode="quick_lookup")
    assert status == 201 and set(body) == {"id"}
    status, listed = spark.req("research_list", {}, a)
    assert status == 200 and [t["id"] for t in listed["tasks"]] == [body["id"]]
    assert set(listed["tasks"][0]) == {"id", "objective", "status", "stage", "mode", "created", "updated"}
    assert listed["tasks"][0]["status"] == "queued" and listed["tasks"][0]["mode"] == "quick_lookup"
    status, task = spark.req("research_get", {"id": body["id"]}, a)
    assert status == 200 and task["objective"] == QUESTION and task["report"] is None
    status, task = spark.req("research_cancel", {"id": body["id"]}, a)
    assert status == 200 and task["status"] == "cancelled"
    second = submit(spark, a, "Compare Zephyr 4.2 and Zephyr 4.1 build speed", mode="comparative")[1]["id"]
    assert [t["id"] for t in spark.req("research_list", {}, a)[1]["tasks"]] == [second, body["id"]]
    with spark.owner() as db:
        row = orchestrator.get(db, second)
    assert row["origin"] == "pocket" and row["family"] and row["status"] == "queued"


def test_family_scoping_and_owner_view(spark, devices):
    a, b, c = devices
    first = submit(spark, a)[1]["id"]
    assert [t["id"] for t in spark.req("research_list", {}, b)[1]["tasks"]] == [first]
    assert spark.req("research_list", {}, c)[1]["tasks"] == []
    assert spark.req("research_get", {"id": first}, c)[0] == 404
    assert spark.req("research_cancel", {"id": first}, c)[0] == 404
    assert spark.req("research_get", {"id": "6f1c1f0e-8c9a-4f63-9f0e-3f3f2c1b2a10"}, a)[0] == 404   # unknown id
    own = submit(spark, c, "Zephyr compiler supported targets")[1]["id"]
    assert [t["id"] for t in spark.req("research_list", {}, c)[1]["tasks"]] == [own]
    assert spark.req("research_get", {"id": own}, a)[0] == 404
    status, cancelled = spark.req("research_cancel", {"id": first}, b)
    assert status == 200 and cancelled["status"] == "cancelled"
    assert spark.req("research_get", {"id": first}, a)[1]["status"] == "cancelled"
    with spark.owner() as db:
        assert {t["id"] for t in orchestrator.listing(db)} == {first, own}
        digests = {t["id"]: t["family"] for t in orchestrator.listing(db)}
    assert digests[first] != digests[own] and len(digests[first]) == 16
    for token in (a, b):
        assert token not in digests[first] and bridge.fingerprint(token) not in digests[first]
    # Erasing the originating phone leaves the task with the individual and visible to the linked tablet.
    assert spark.req("unpair", {}, a)[0] == 200
    assert spark.req("research_list", {}, a)[0] == 403
    assert [t["id"] for t in spark.req("research_list", {}, b)[1]["tasks"]] == [first]
    with spark.owner() as db:
        assert orchestrator.get(db, first)["status"] == "cancelled"


def test_request_rules(spark, devices):
    a, _, _ = devices
    big = json.dumps({"objective": "x" * 600})
    assert spark.req("research_submit", token=a, raw=big)[0] == 400
    assert submit(spark, a, "y" * 301)[0] == 400
    assert submit(spark, a, "   ")[0] == 400
    assert submit(spark, a, QUESTION, budget={"max_pages": 50})[0] == 400
    assert submit(spark, a, QUESTION, mode="continuous")[0] == 400
    assert submit(spark, a, QUESTION, mode=None)[0] == 400
    assert spark.req("research_list", {"family": "x"}, a)[0] == 400
    assert spark.req("research_get", {"id": "not-a-uuid"}, a)[0] == 400
    assert spark.req("research_get", {"id": "6f1c1f0e-8c9a-4f63-9f0e-3f3f2c1b2a10", "x": 1}, a)[0] == 400
    assert submit(spark, a, "Write to jane.doe@example.com about Zephyr") == (400, {"error": "private_data"})
    assert submit(spark, "not-a-credential")[0] == 403
    assert spark.req("research_list", {})[0] == 403
    assert spark.req("research_list", {}, a, origin="http://evil.example")[0] == 403
    assert spark.req("research_list", {}, a, origin="http://" + spark.host)[0] == 200
    with spark.owner() as db:
        assert orchestrator.listing(db) == [], "nothing was queued by a rejected request"


def test_rate_limit_per_device(spark, devices):
    a, b, _ = devices
    for n in range(bridge.SUBMISSIONS_PER_HOUR):
        assert submit(spark, a, f"Zephyr compiler question {n}", mode="quick_lookup")[0] == 201
    assert submit(spark, a, "Zephyr compiler question 11", mode="quick_lookup")[0] == 429
    assert submit(spark, b, "Zephyr compiler question 12", mode="quick_lookup")[0] == 201, "per device"
    assert len(spark.req("research_list", {}, b)[1]["tasks"]) == 11


def test_scenario_f_phone_queues_spark_runs_tablet_reads(spark, devices, web):
    a, b, c = devices
    task_id = submit(spark, a, QUESTION)[1]["id"]
    queued = submit(spark, a, "Zephyr compiler build time improvements", mode="quick_lookup")[1]["id"]
    assert spark.req("research_cancel", {"id": queued}, b)[1]["status"] == "cancelled"   # tablet cancels
    out = scheduler.work(spark.instance, once=True, fixture=web, providers=[corpus()])
    assert [(p["task_id"], p["status"]) for p in out["processed"]] == [(task_id, "completed")]
    status, done = spark.req("research_get", {"id": task_id}, b)
    assert status == 200 and done["status"] == "completed" and done["stage"] is None
    assert done["stages_done"] == ["discovering", "reading", "evaluating", "synthesizing"]
    report = done["report"]
    assert set(report) == {"live", "answer", "contradictions", "uncertainties", "limitations", "freshness", "sources"}
    assert report["live"] is True and len(report["sources"]) == 3
    assert {s["retrieval_mode"] for s in report["sources"]} == {"live"}
    assert all(set(s) == {"n", "title", "url", "domain", "published_at", "retrieved_at", "retrieval_mode"}
               for s in report["sources"])
    quotes = [x for x in report["answer"] if x["type"] == "quote"]
    assert quotes and "4.2.0" in quotes[0]["text"] and quotes[0]["citations"]
    assert {n for x in report["answer"] for n in x["citations"]} <= {s["n"] for s in report["sources"]}
    assert len(json.dumps(done)) < 64_000
    assert spark.req("research_get", {"id": task_id}, c)[0] == 404
    assert [t["status"] for t in spark.req("research_list", {}, b)[1]["tasks"]] == ["cancelled", "completed"]
    with spark.owner() as db:
        owner = {t["id"]: t["status"] for t in orchestrator.listing(db)}
        full = orchestrator.get(db, task_id)
    assert owner == {task_id: "completed", queued: "cancelled"}
    assert "usage" in full["result"] and "usage" not in report, "the phone gets a trimmed report"
    assert spark.req("research_cancel", {"id": task_id}, b)[1]["status"] == "completed"   # terminal stays


def test_main_rejects_missing_instance(tmp_path, capsys):
    with pytest.raises(SystemExit):
        bridge.main(["--db", str(tmp_path / "m.sqlite"), "--instance-db", str(tmp_path / "missing.sqlite")])
    assert "cannot open instance" in capsys.readouterr().err
