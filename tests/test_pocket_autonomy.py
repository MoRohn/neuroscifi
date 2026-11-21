"""Pocket research in 0.7.0 over the real in-process HTTP bridge: evidence review (ADR-035 state/basis), continue on
another device of the same family, the user's daily budget (D4), and research nodes with explicit session hand-off
(RD-11 minimal scope; one executing node, ADR-031). No cross-family leakage; bodies stay within 512 bytes."""
from concurrent.futures import ThreadPoolExecutor
import json
import time

import pytest

from neuroscifi.pocket import server as bridge
from neuroscifi.webcortex import autonomy, nodes, orchestrator, scheduler, state
from test_pocket_research import PAGES, QUESTION, Spark, corpus, html, submit
from neuroscifi.webcortex.fixtureweb import FixtureWeb


@pytest.fixture
def web():
    w = FixtureWeb().start()
    for (host, path), (title, date, paragraphs) in PAGES.items():
        w.add(host, path, html(title, date, paragraphs))
    try:
        yield w
    finally:
        w.stop()


@pytest.fixture
def spark(tmp_path):
    s = Spark(tmp_path)
    yield s
    s.close()


@pytest.fixture
def devices(spark):
    """Phone A paired by invitation, tablet B linked by A's handoff code, unrelated C paired separately."""
    a = spark.req("pair", {"code": "invite"})[1]["key"]
    b = spark.req("pair", {"code": spark.req("link", {}, a)[1]["code"]})[1]["key"]
    return a, b, spark.server.store.pair()


def enrol(spark, token, label):
    with spark.owner() as db:
        nodes.enrol(db, bridge.fingerprint(token), spark.server.store.family(token), label, time.time())


@pytest.fixture
def family(spark, devices):
    """Devices A and B (nodes 'phone' and 'laptop'), D (linked to A, not a node), C (another family)."""
    a, b, c = devices
    code = spark.req("link", {}, a)[1]["code"]
    d = spark.req("pair", {"code": code})[1]["key"]
    enrol(spark, a, "phone")
    enrol(spark, b, "laptop")
    return a, b, c, d


def test_new_routes_are_invisible_without_a_credential_and_when_off(spark, devices):
    a, _, _ = devices
    for route in ("research_evidence", "research_continue", "research_session"):
        assert spark.req(route, {"id": "x"}) == (403, {})
        assert spark.req(route, {"id": "x"}, "not-a-credential") == (403, {})
    spark.configure(pocket_research=False)
    for route in ("research_evidence", "research_continue", "research_session"):
        assert spark.req(route, {"id": "x"}, a)[0] == 404


def test_evidence_review_and_family_isolation(spark, devices, web):
    a, b, c = devices
    task_id = submit(spark, a, QUESTION)[1]["id"]
    scheduler.work(spark.instance, once=True, fixture=web, providers=[corpus()])
    status, review = spark.req("research_evidence", {"id": task_id}, b)
    assert status == 200 and review["status"] == "completed" and review["evidence"]
    assert {e["state"] for e in review["evidence"]} <= {"supported", "disputed", "insufficient", "conflicting",
                                                        "temporally_unresolved", "not_evaluated"}
    assert any(e["state"] == "supported" and e["basis"] for e in review["evidence"])
    assert all(e["untrusted"] is True and len(e["statement"]) <= 300 for e in review["evidence"])
    assert len(review["sources"]) == 3
    assert spark.req("research_evidence", {"id": task_id}, c) == (404, {})
    status, detail = spark.req("research_get", {"id": task_id}, b)
    assert detail["spent"]["searches"] >= 1 and detail["spent"]["pages"] == 3


def test_evidence_review_counts_the_sites_that_state_each_subject(spark, devices, web):
    """R2-06: Pocket said "no supporting evidence was found" for a statement quoted from a listed source. Each subject
    now carries how many sites state it, so the phone can say "stated by one source only"."""
    a, _, _ = devices
    task_id = submit(spark, a, QUESTION)[1]["id"]
    scheduler.work(spark.instance, once=True, fixture=web, providers=[corpus()])
    review = spark.req("research_evidence", {"id": task_id}, a)[1]
    assert all(isinstance(e["sources"], int) for e in review["evidence"])
    assert all(e["sources"] >= 1 for e in review["evidence"] if e["statement"])
    insufficient = [e for e in review["evidence"] if e["state"] == "insufficient" and e["statement"]]
    assert insufficient and all(e["sources"] == 1 for e in insufficient)       # each quoted from one listed site
    supported = [e for e in review["evidence"] if e["state"] == "supported" and e["basis"] == "corroboration"]
    assert all(e["sources"] >= 2 for e in supported)


def test_continue_on_another_device_of_the_family(spark, devices, web):
    a, b, c = devices
    first = submit(spark, a, QUESTION)[1]["id"]
    assert spark.req("research_cancel", {"id": first}, a)[1]["status"] == "cancelled"
    assert spark.req("research_continue", {"id": first}, c) == (404, {})          # another family
    status, cont = spark.req("research_continue", {"id": first}, b)                # the tablet continues it
    assert status == 201 and cont["requeued"] == "child" and cont["id"] != first
    assert spark.req("research_continue", {"id": first}, a)[1]["requeued"] == "existing"
    scheduler.work(spark.instance, once=True, fixture=web, providers=[corpus()])
    status, done = spark.req("research_get", {"id": cont["id"]}, a)                 # results sync to the phone
    assert status == 200 and done["status"] == "completed" and done["continued_from"] == first
    assert [t["id"] for t in spark.req("research_list", {}, c)[1]["tasks"]] == []
    assert spark.req("research_continue", {"id": cont["id"]}, a) == (409, {"error": "not_continuable"})


def test_the_owners_daily_user_budget_refuses_with_a_code(spark, devices):
    a, _, _ = devices
    with spark.owner() as db:
        autonomy.set_policy(db, {"user_daily": {"searches": 0}}, time.time())
    assert submit(spark, a, QUESTION) == (429, {"error": "daily_budget"})
    with spark.owner() as db:
        assert orchestrator.listing(db) == []
        autonomy.set_policy(db, {"user_daily": {"searches": 400}}, time.time())
    assert submit(spark, a, QUESTION)[0] == 201


def test_node_session_take_handoff_accept(spark, family):
    a, b, c, d = family
    task_id = submit(spark, a, QUESTION)[1]["id"]
    assert spark.req("research_session", {"id": task_id, "action": "status"}, d)[1]["owner"] is None
    status, view = spark.req("research_session", {"id": task_id, "action": "take"}, a)
    assert status == 200 and view["owner"] == "phone" and view["you"] is True
    assert spark.req("research_session", {"id": task_id, "action": "take"}, b)[1]["error"] == "owned"
    assert spark.req("research_session", {"id": task_id, "action": "take"}, d) == (409, {"error": "not_a_node"})
    assert spark.req("research_cancel", {"id": task_id}, b)[1]["error"] == "owned"
    assert spark.req("research_cancel", {"id": task_id}, d)[1]["error"] == "owned"
    assert spark.req("research_session", {"id": task_id, "action": "accept"}, b)[1]["error"] == "owned"
    assert spark.req("research_session", {"id": task_id, "action": "handoff", "to": "nobody"}, a) == \
        (409, {"error": "no_such_node"})
    status, offered = spark.req("research_session", {"id": task_id, "action": "handoff", "to": "laptop"}, a)
    assert status == 200 and offered["owner"] == "phone" and offered["offered_to"] == "laptop"
    status, taken = spark.req("research_session", {"id": task_id, "action": "accept"}, b)
    assert status == 200 and taken["owner"] == "laptop" and taken["you"] is True
    assert spark.req("research_session", {"id": task_id, "action": "status"}, a)[1]["you"] is False
    assert spark.req("research_cancel", {"id": task_id}, a)[1]["error"] == "owned"
    assert spark.req("research_cancel", {"id": task_id}, b)[1]["status"] == "cancelled"
    assert spark.req("research_session", {"id": task_id, "action": "status"}, c) == (404, {})
    assert spark.req("research_session", {"id": task_id, "action": "release"}, a)[1]["error"] == "owned"
    assert spark.req("research_session", {"id": task_id, "action": "release"}, b)[1]["owner"] is None
    with spark.owner() as db:     # nodes never execute: the Spark's worker is the one executing node
        task = state.task(db, task_id)
        assert task["lease_holder"] is None and task["executed_on"] is None
        assert db.execute("SELECT count(*) FROM autonomy_sessions").fetchone()[0] == 0


def test_never_two_owners_under_a_race(spark, family):
    a, b, _, _ = family
    task_id = submit(spark, a, QUESTION)[1]["id"]
    with ThreadPoolExecutor(8) as pool:
        results = list(pool.map(lambda tok: spark.req("research_session", {"id": task_id, "action": "take"}, tok),
                                [a, b] * 4))
    winners = {r[1]["owner"] for r in results if r[0] == 200}
    assert len(winners) == 1
    with spark.owner() as db:
        assert db.execute("SELECT count(*) FROM autonomy_sessions").fetchone()[0] == 1


def test_a_revoked_node_loses_its_sessions_and_the_continuation_stays_in_the_session(spark, family, web):
    a, b, _, _ = family
    task_id = submit(spark, a, QUESTION)[1]["id"]
    spark.req("research_session", {"id": task_id, "action": "take"}, a)
    assert spark.req("research_cancel", {"id": task_id}, a)[1]["status"] == "cancelled"
    cont = spark.req("research_continue", {"id": task_id}, a)[1]["id"]
    assert spark.req("research_session", {"id": cont, "action": "status"}, b)[1]["owner"] == "phone"   # same session
    assert spark.req("research_continue", {"id": task_id}, b)[1]["error"] == "owned"
    with spark.owner() as db:
        assert nodes.revoke(db, "phone", time.time()) == 1
    assert spark.req("research_session", {"id": cont, "action": "take"}, a) == (409, {"error": "not_a_node"})
    assert spark.req("research_session", {"id": cont, "action": "take"}, b)[1]["owner"] == "laptop"


def test_bodies_stay_within_512_bytes_and_are_validated(spark, family):
    a, _, _, _ = family
    task_id = submit(spark, a, QUESTION)[1]["id"]
    big = json.dumps({"id": task_id, "action": "handoff", "to": "x" * 600})
    assert spark.req("research_session", raw=big, token=a)[0] == 400
    for body in ({"id": task_id, "action": "fly"}, {"id": task_id, "action": "take", "to": "laptop"},
                 {"id": task_id, "action": "handoff"}, {"id": task_id, "extra": 1}, {"id": "not-a-uuid"}):
        assert spark.req("research_session", body, a)[0] == 400, body
    assert spark.req("research_evidence", {"id": task_id, "x": 1}, a)[0] == 400
    assert spark.req("research_continue", {}, a)[0] == 400
