"""Web memory inside the individual: the executive question path, forgetting and erasure, memory retrieval,
inventory. Mirrors tests/instance/test_erasure.py: forgotten text must not survive in the file or its WAL."""
import json

import pytest

from neuroscifi.instance import evidence, executive, governance, memory, store
from neuroscifi.instance.errors import Conflict, ValidationError
from neuroscifi.webcortex import consolidate, knowledge, predictions, scheduler, state
from test_consolidate import DAY, T0, claim, cluster, consolidated, task_with, two_sources, open_db


@pytest.fixture
def db(tmp_path):
    yield from open_db(tmp_path)

SECRET_OBJECTIVE = "What is the latest numpy release version for quokkazz-project?"


def raw_bytes(path):
    data = path.read_bytes()
    wal = path.with_name(path.name + "-wal")
    return data + (wal.read_bytes() if wal.exists() else b"")


def db_path(db):
    return __import__("pathlib").Path(db.execute("PRAGMA database_list").fetchone()[2])


def checkpoint_row(db):
    with store.write(db):
        db.execute("INSERT INTO checkpoints(path,policy,sha256,bytes,state_version,max_evidence_seq,created_at,"
                   "status) VALUES('/x.nsfa','cold','h',1,1,0,?,'valid')", (T0,))


def question(db, kid):
    return db.execute("SELECT id FROM pending WHERE scope=?", (f"web:knowledge:{kid}",)).fetchone()[0]


# ---- executive ---------------------------------------------------------------------------------------------------

def test_resolve_confirm_accepts_and_reject_rejects_in_one_transaction(db):
    _, out = consolidated(db, [two_sources()])
    kid = out["provisional"][0]
    pid = question(db, kid)
    teacher = store.counter(db, "teacher_interventions")
    done = executive.resolve(db, pid, "yes", T0 + 5, confirm=True)
    assert done["web"] == {"kind": "knowledge", "id": kid, "previous_status": "provisional", "status": "accepted"}
    assert done["resolved_at"] == T0 + 5 and done["resolution"] == "yes"
    assert knowledge.row(db, kid)["status"] == "accepted"
    assert store.counter(db, "teacher_interventions") == teacher + 1
    assert executive.resolve(db, pid, "yes", T0 + 6, confirm=True)["resolved_at"] == T0 + 5   # idempotent
    with pytest.raises(Conflict):
        executive.resolve(db, pid, "no", T0 + 7, confirm=False)

    _, other = consolidated(db, [two_sources_key("pandas latest version", "pandas 3.0.1 is the latest pandas release.",
                                                 ("c.example", "d.example"))], T0 + 10)
    rejected = other["provisional"][0]
    executive.resolve(db, question(db, rejected), "no", T0 + 11, confirm=False)
    assert knowledge.row(db, rejected)["status"] == "rejected"


def two_sources_key(key, text, hosts):
    return cluster([claim(f"http://{h}/p", text, key=key) for h in hosts])


def test_informational_web_questions_take_no_confirm_and_do_not_decay(db):
    a, b = claim("http://a.example/n"), claim("http://b.example/n", "NumPy 2.4.0 is the latest release.")
    contested = cluster([a, b], status="contested", confidence=0.5,
                        contradicting=[{"a": a.id, "b": b.id, "reason": "different versions"}])
    consolidated(db, [contested])
    pid = db.execute("SELECT id FROM pending WHERE scope LIKE 'web:contested:%'").fetchone()[0]
    with pytest.raises(ValidationError, match="informational"):
        executive.resolve(db, pid, "the newer one", T0 + 1, confirm=True)
    assert db.execute("SELECT resolved_at FROM pending WHERE id=?", (pid,)).fetchone() == (None,)
    # An epoch decision 8 days later expires stale epoch questions but leaves web questions open.
    evidence.start_epoch(db, "e1", T0)
    with store.write(db):
        db.execute("INSERT INTO pending(epoch,kind,scope,question,created_at) VALUES('e1','ask','mechanism','?',?)",
                   (T0,))
    out = executive.decide(db, "e1", T0 + 8 * DAY, record=True)
    assert pid not in out["expired_pending"] and out["expired_pending"]
    assert executive.resolve(db, pid, "noted", T0 + 9 * DAY)["resolved_at"] == T0 + 9 * DAY


def test_pocket_monitor_proposal_is_authorised_through_resolve(db):
    mon = scheduler.monitor_add(db, "numpy releases", T0, interval=7200, authorized_by="pocket:" + "ab" * 8)
    assert mon["status"] == "proposed" and mon["next_run_at"] is None
    done = executive.resolve(db, mon["pending_id"], "ok", T0 + 1, confirm=True)
    assert done["web"] == {"kind": "monitor", "id": mon["id"], "status": "active"}
    assert scheduler.monitor_list(db)[0]["next_run_at"] == T0 + 1
    other = scheduler.monitor_add(db, "pandas releases", T0, interval=7200, authorized_by="pocket:" + "cd" * 8)
    executive.resolve(db, other["pending_id"], "no", T0 + 2, confirm=False)
    assert scheduler.monitor_list(db, status="stopped")[0]["id"] == other["id"]


# ---- governance --------------------------------------------------------------------------------------------------

def test_forget_research_erases_the_task_and_knowledge_resting_on_it(db):
    path = db_path(db)
    checkpoint_row(db)
    tid = task_with(db, [two_sources()], objective=SECRET_OBJECTIVE)
    with store.write(db):
        state.log_event(db, tid, "query", {"text": SECRET_OBJECTIVE}, T0)
    out = consolidate.run(db, tid, T0)
    kid = out["provisional"][0]
    predictions.record(db, kid, "still holds at next refresh", T0 + DAY, T0)
    pid = question(db, kid)
    assert b"quokkazz" in raw_bytes(path)

    result = governance.forget_research(db, tid, T0 + 10)
    effects = result["effects"]
    assert result["status"] == "forgotten" and effects["knowledge_deleted"] == [kid]
    assert effects["checkpoints_stale"] == [1] and effects["wal_checkpoint"] == "ok"
    assert pid in effects["pending_closed"]
    for table in ("web_tasks", "web_events", "web_sources", "web_claims", "web_claim_links", "web_knowledge",
                  "web_predictions", "web_feedback", "web_mentions", "web_entities"):
        assert db.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0, table
    assert db.execute("SELECT question, resolution FROM pending WHERE id=?", (pid,)).fetchone() == (
        "#forgotten", governance.WEB_FORGOTTEN)
    log = db.execute("SELECT target_kind, target_id, effects FROM deletion_log").fetchall()
    assert [(k, t) for k, t, _ in log] == [("research", tid)] and "quokkazz" not in log[0][2]
    assert db.execute("SELECT status FROM checkpoints").fetchone() == ("stale",)
    assert b"quokkazz" not in raw_bytes(path) and b"NumPy 2.3.5" not in raw_bytes(path)
    assert governance.forget_research(db, tid, T0 + 11)["status"] == "absent"


def test_forget_research_keeps_knowledge_with_other_support_as_stale(db):
    _, first = consolidated(db, [two_sources()])
    kid = first["provisional"][0]
    second, _ = consolidated(db, [two_sources(hosts=("c.example", "d.example"))], T0 + DAY)
    assert len(knowledge.row(db, kid)["supporting"]) == 4
    governance.forget_research(db, first["task_id"], T0 + 2 * DAY)
    item = knowledge.row(db, kid)
    assert item["status"] == "stale" and len(item["supporting"]) == 2 and "re-verification" in item["note"]
    assert knowledge.query(db, "numpy latest", T0 + 2 * DAY)["items"][0]["stale"] is True


def test_forget_knowledge_removes_the_whole_version_chain(db):
    _, first = consolidated(db, [two_sources(published=T0 - 10 * DAY)])
    _, second = consolidated(db, [two_sources(published=T0 - DAY, text="NumPy 2.4.0 is the latest release of NumPy.",
                                              hosts=("c.example", "d.example"))], T0 + DAY)
    old, new = first["provisional"][0], second["superseded"][0]["new"]
    predictions.record(db, new, "still holds at next refresh", T0 + 9 * DAY, T0 + DAY)
    out = knowledge.forget(db, old, T0 + 2 * DAY)
    assert sorted(out["effects"]["knowledge_deleted"]) == sorted([old, new])
    assert db.execute("SELECT count(*) FROM web_knowledge").fetchone()[0] == 0
    assert db.execute("SELECT count(*) FROM web_predictions").fetchone()[0] == 0
    assert db.execute("SELECT count(*) FROM pending WHERE resolved_at IS NULL").fetchone()[0] == 0
    assert db.execute("SELECT count(*) FROM web_claims").fetchone()[0] == 4   # evidence stays with its tasks
    assert db.execute("SELECT target_kind, target_id FROM deletion_log").fetchall() == [("knowledge", str(old))]


def test_forget_source_by_domain_and_url(db):
    _, out = consolidated(db, [two_sources(hosts=("a.example", "news.b.example"))])
    kid = out["provisional"][0]
    with store.write(db):
        state.save_source(db, out["task_id"], {"url": "http://a.example/numpy", "canonical_url": "http://a.example/numpy",
                                               "domain": "a.example", "retrieved_at": T0, "content_sha256": "ab" * 32},
                          T0)
    gone = governance.forget_source(db, "a.example", T0 + 1)
    effects = gone["effects"]
    assert effects["cache"] == {"urls": ["http://a.example/numpy"], "domain": "a.example", "sha256": ["ab" * 32]}
    assert effects["knowledge_stale"] == [kid] and effects["sources_deleted"] == 1
    item = knowledge.row(db, kid)
    assert [s["url"] for s in item["sources"]] == ["http://news.b.example/numpy"] and item["status"] == "stale"
    last = governance.forget_source(db, "http://news.b.example/numpy", T0 + 2)
    assert last["effects"]["knowledge_deleted"] == [kid]
    assert governance.forget_source(db, "c.example", T0 + 3)["status"] == "absent"
    assert [t for (t,) in db.execute("SELECT target_kind FROM deletion_log")] == ["source", "source"]


# ---- memory and inventory ----------------------------------------------------------------------------------------

def test_memory_retrieves_web_kinds_with_filters(db):
    tid, out = consolidated(db, [two_sources()])
    kid = out["provisional"][0]
    with store.write(db):
        state.save_source(db, tid, {"url": "http://a.example/numpy", "canonical_url": "http://a.example/numpy",
                                    "domain": "a.example", "retrieved_at": T0, "title": "NumPy"}, T0)
    scheduler.monitor_add(db, "numpy releases", T0, interval=3600, authorized_by="owner")
    keys = {"kind", "id", "scope", "time", "permissions", "confidence", "outcome_history", "payload"}
    for kind in ("research", "claim", "knowledge", "source", "monitor"):
        items = memory.retrieve(db, kind)["items"]
        assert items and all(set(i) == keys and i["permissions"]["consent"] == "web" for i in items), kind
        json.dumps(items, allow_nan=False)
    research = memory.retrieve(db, "research", id=tid)["items"][0]
    assert research["payload"]["status"] == "queued" and research["id"] == tid
    item = memory.retrieve(db, "knowledge", status="provisional")["items"][0]
    assert item["id"] == kid and item["confidence"] == 0.85 and item["payload"]["contested"] is False
    assert memory.retrieve(db, "knowledge", status="accepted")["items"] == []
    claims = memory.retrieve(db, "claim", task=tid, status="corroborated")["items"]
    assert len(claims) == 2 and claims[0]["payload"]["untrusted"] is True and claims[0]["confidence"] == 0.85
    assert memory.retrieve(db, "source", task=tid)["items"][0]["payload"]["domain"] == "a.example"
    assert memory.retrieve(db, "monitor", status="active")["items"][0]["payload"]["topic"] == "numpy releases"
    for bad in ({"kind": "evidence", "status": "x"}, {"kind": "knowledge", "task": tid},
                {"kind": "knowledge", "id": "abc"}):
        kind = bad.pop("kind")
        with pytest.raises(ValidationError):
            memory.retrieve(db, kind, **bad)


def test_inventory_counts_web_memory(db):
    consolidated(db, [two_sources()])
    web = store.inventory(db)["web"]
    assert web["tasks"] == 1 and web["claims"] == 2 and web["knowledge"] == 1 and web["provisional_knowledge"] == 1
    assert web["open_tasks"] == 1 and web["accepted_knowledge"] == 0
