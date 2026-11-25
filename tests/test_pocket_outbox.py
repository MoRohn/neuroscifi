"""Pocket's outbox (ENGINEERING_PLAN.md §6 item 12): a question written while the Spark is out of reach is sent later
with a request key, and the Spark queues it exactly once, however often the phone retries after a lost reply."""
from contextlib import closing
import json
import threading
import time
import uuid

from neuroscifi.pocket import server as bridge
from neuroscifi.webcortex import orchestrator, signing, state
from nodekeys import enrol
from test_pocket_research import QUESTION, Spark, submit

import pytest


@pytest.fixture
def spark(tmp_path):
    s = Spark(tmp_path)
    yield s
    s.close()


@pytest.fixture
def devices(spark):
    status, a = spark.req("pair", {"code": "invite"})
    assert status == 201
    b = spark.req("pair", {"code": spark.req("link", {}, a["key"])[1]["code"]})[1]["key"]
    spark.pin = a["spark"]
    return a["key"], b, spark.server.store.pair()


def tasks(spark):
    with spark.owner() as db:
        return orchestrator.listing(db)


def submissions(spark):
    with closing(bridge.connect(spark.server.store.path)) as db:
        return db.execute("SELECT count(*) FROM submissions").fetchone()[0]


def test_a_retried_request_key_queues_one_task_and_counts_once(spark, devices):
    a, _, _ = devices
    key = str(uuid.uuid4())
    status, first = submit(spark, a, QUESTION, request=key)
    assert status == 201 and set(first) == {"id"}
    for _ in range(3):                                   # the reply was lost: the phone sends the same request again
        assert submit(spark, a, QUESTION, request=key) == (200, {"id": first["id"], "replayed": True})
    assert [t["id"] for t in tasks(spark)] == [first["id"]]
    assert submissions(spark) == 1, "a replay is not counted against the hourly limit"


def test_a_replay_is_answered_after_the_task_finished(spark, devices):
    """orchestrator.duplicate only merges identical questions while one is in flight; the key holds after it ends."""
    a, _, _ = devices
    key = str(uuid.uuid4())
    task = submit(spark, a, QUESTION, request=key)[1]["id"]
    with spark.owner() as db:
        orchestrator.cancel(db, task, time.time())
    assert submit(spark, a, QUESTION, request=key) == (200, {"id": task, "replayed": True})
    assert len(tasks(spark)) == 1


def test_the_key_is_kept_in_the_individual_so_a_lost_pocket_record_still_replays(spark, devices):
    """A crash between the individual's commit and the Pocket database's record: the task is found by its tag."""
    a, _, _ = devices
    key = str(uuid.uuid4())
    task = submit(spark, a, QUESTION, request=key)[1]["id"]
    with closing(bridge.connect(spark.server.store.path)) as db, db:
        db.execute("DELETE FROM outbox_requests")
    assert submit(spark, a, QUESTION, request=key) == (200, {"id": task, "replayed": True})
    with closing(bridge.connect(spark.server.store.path)) as db:
        assert db.execute("SELECT count(*) FROM outbox_requests").fetchone()[0] == 1, "the record is restored"
    with spark.owner() as db:
        detail = [e["detail"] for e in state.events(db, task) if e["kind"] == "submitted"][0]
        digest = state.family_digest(db, spark.server.store.family(a))
    assert key not in json.dumps(detail), "the individual keeps a family-bound tag, never the device's key"
    assert detail["request"] == bridge.request_tag(digest, key)


def test_a_forgotten_task_is_not_queued_again(spark, devices):
    a, _, _ = devices
    key = str(uuid.uuid4())
    task = submit(spark, a, QUESTION, request=key)[1]["id"]
    from neuroscifi.instance import governance
    with spark.owner() as db:
        governance.forget_research(db, task, time.time())
    assert submit(spark, a, QUESTION, request=key) == (410, {"error": "forgotten"})
    assert tasks(spark) == []


def test_keys_are_scoped_to_the_family(spark, devices):
    """A linked device shares its family's keys (the same phone owner); another family's identical key is its own."""
    a, b, c = devices
    key = str(uuid.uuid4())
    task = submit(spark, a, QUESTION, request=key)[1]["id"]
    assert submit(spark, b, QUESTION, request=key) == (200, {"id": task, "replayed": True})
    status, other = submit(spark, c, QUESTION, request=key)
    assert status == 201 and other["id"] != task
    assert spark.req("research_get", {"id": task}, c)[0] == 404


def test_request_key_rules(spark, devices):
    a, _, _ = devices
    for bad in ("not-a-uuid", str(uuid.uuid4()).upper(), str(uuid.uuid1()), 7, None, ""):
        assert submit(spark, a, QUESTION, request=bad)[0] == 400, bad
    assert tasks(spark) == []
    question = "量子" * 150                       # the largest question still fits with its key and the longest mode
    body = json.dumps({"objective": question, "mode": "fact_verification", "request": str(uuid.uuid4())},
                      ensure_ascii=False).encode()
    assert len(body) <= bridge.RESEARCH_BODY_LIMIT
    assert spark.req("research_submit", token=a, raw=body)[0] == 201


def test_refusals_queue_nothing_and_record_no_key(spark, devices):
    """A refused request (private data, the hourly limit) may be sent again with the same key once it can pass."""
    a, _, _ = devices
    key = str(uuid.uuid4())
    assert submit(spark, a, "Write to jane.doe@example.com about Zephyr", request=key) == \
        (400, {"error": "private_data"})
    for n in range(bridge.SUBMISSIONS_PER_HOUR):
        assert submit(spark, a, f"Zephyr compiler question {n}", mode="quick_lookup")[0] == 201
    limited = str(uuid.uuid4())
    assert submit(spark, a, QUESTION, request=limited)[0] == 429
    with closing(bridge.connect(spark.server.store.path)) as db:
        assert db.execute("SELECT count(*) FROM outbox_requests").fetchone()[0] == 0
    spark.server.store.clock = lambda: time.time() + 3601           # an hour later the same key goes through once
    status, sent = submit(spark, a, QUESTION, request=limited)
    assert status == 201
    assert submit(spark, a, QUESTION, request=limited) == (200, {"id": sent["id"], "replayed": True})


def test_concurrent_retries_queue_one_task(spark, devices):
    a, _, _ = devices
    key, results = str(uuid.uuid4()), []
    threads = [threading.Thread(target=lambda: results.append(submit(spark, a, QUESTION, request=key)))
               for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    ids = {body["id"] for status, body in results}
    assert len(ids) == 1 and sorted(s for s, _ in results) == [200] * 5 + [201]
    assert len(tasks(spark)) == 1 and submissions(spark) == 1


def test_keys_expire_with_the_outbox_and_with_the_family(spark, devices):
    a, _, _ = devices
    submit(spark, a, QUESTION, request=str(uuid.uuid4()))
    store = spark.server.store
    store.clock = lambda: time.time() + (bridge.REQUEST_DAYS * bridge.DAY) - 60
    store.sweep()
    with closing(bridge.connect(store.path)) as db:
        assert db.execute("SELECT count(*) FROM outbox_requests").fetchone()[0] == 1
    store.clock = lambda: time.time() + bridge.REQUEST_DAYS * bridge.DAY + 60
    store.sweep()
    with closing(bridge.connect(store.path)) as db:
        assert db.execute("SELECT count(*) FROM outbox_requests").fetchone()[0] == 0
    store.clock = time.time
    submit(spark, a, "Another Zephyr question", request=str(uuid.uuid4()))
    assert spark.req("unpair", {"family": True}, a)[0] == 200
    store.sweep()
    with closing(bridge.connect(store.path)) as db:
        assert db.execute("SELECT count(*) FROM outbox_requests").fetchone()[0] == 0, "erased family leaves no key"


@pytest.mark.parametrize("alg", [signing.ED25519, signing.P256])
def test_a_signed_node_resends_with_a_fresh_signature_and_is_replayed(spark, devices, alg):
    """The key is inside the signed body. A queued question is signed again whenever it is sent, so a signature
    never outlives the freshness window; the very same signed bytes twice are refused as a replay; a key changed
    inside a signed body breaks the signature."""
    a, _, _ = devices
    key = enrol(spark, a, "phone", alg)
    request = str(uuid.uuid4())
    body = json.dumps({"objective": QUESTION, "request": request})
    fixed = key.headers("research_submit", body.encode())
    status, first = spark.req("research_submit", token=a, raw=body, headers=fixed, sign=False)
    assert status == 201
    assert spark.req("research_submit", token=a, raw=body, headers=fixed, sign=False) == \
        (401, {"error": "replayed_request"})
    old = key.headers("research_submit", body.encode(), timestamp=time.time() - 3600)   # signed an hour ago
    assert spark.req("research_submit", token=a, raw=body, headers=old, sign=False) == \
        (401, {"error": "stale_request"})
    assert submit(spark, a, QUESTION, request=request) == (200, {"id": first["id"], "replayed": True})  # re-signed
    forged = body.replace(request, str(uuid.uuid4()))
    sent = key.headers("research_submit", body.encode())
    assert spark.req("research_submit", token=a, raw=forged, headers=sent, sign=False) == \
        (401, {"error": "signature_invalid"})
    assert len(tasks(spark)) == 1
