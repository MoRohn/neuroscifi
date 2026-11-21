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


# ---- review remediation V2: forgetting survives checkpoint restore, logs carry no payload ---------------------

def _restore(db, tmp_path, forget):
    """Checkpoint -> forget (callable) -> restore into a new file. Returns (restore result, restored path)."""
    from neuroscifi.instance import checkpoints
    path = db_path(db)
    cp = checkpoints.create(db, path, tmp_path / "cps", T0 + 1)
    forget()
    assert db.execute("SELECT status FROM checkpoints WHERE id=?", (cp["id"],)).fetchone()[0] == "stale"
    dest = tmp_path / "restored.sqlite"
    return checkpoints.restore(db, cp["id"], dest, T0 + 3), dest


def test_restore_never_resurrects_forgotten_research(db, tmp_path):
    """A-P0-1: the restored copy of a checkpoint taken before forget_research holds no task, knowledge, question or
    objective byte of it."""
    import sqlite3
    tid, out = consolidated(db, [two_sources()], objective=SECRET_OBJECTIVE)
    result, dest = _restore(db, tmp_path, lambda: governance.forget_research(db, tid, T0 + 2))
    assert result["web_removed"]["tasks"] == 1 and result["web_forgotten_applied"] >= 1
    copy = sqlite3.connect(dest)
    try:
        for table in ("web_tasks", "web_claims", "web_knowledge", "web_events", "web_feedback"):
            assert copy.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0, table
        assert copy.execute("SELECT count(*) FROM pending WHERE scope LIKE 'web:%' AND resolved_at IS NULL"
                            ).fetchone()[0] == 0
    finally:
        copy.close()
    assert b"quokkazz" not in raw_bytes(dest)


def test_restore_never_resurrects_forgotten_knowledge(db, tmp_path):
    import sqlite3
    _, first = consolidated(db, [two_sources(published=T0 - 10 * DAY)])
    _, second = consolidated(db, [two_sources(published=T0 - DAY, text="NumPy 2.4.0 is the latest release of NumPy.",
                                              hosts=("c.example", "d.example"))], T0 + DAY)
    old, new = first["provisional"][0], second["superseded"][0]["new"]
    result, dest = _restore(db, tmp_path, lambda: governance.forget_knowledge(db, old, T0 + 2))
    assert result["web_removed"]["knowledge"] == 2
    copy = sqlite3.connect(dest)
    try:
        assert copy.execute("SELECT count(*) FROM web_knowledge").fetchone()[0] == 0
        assert copy.execute("SELECT count(*) FROM web_predictions").fetchone()[0] == 0
        open_q = copy.execute("SELECT question FROM pending WHERE scope IN (?,?)",
                              (f"web:knowledge:{old}", f"web:knowledge:{new}")).fetchall()
        assert all(q == "#forgotten" for (q,) in open_q)
        assert copy.execute("SELECT count(*) FROM web_claims").fetchone()[0] == 4   # claims stay with their tasks
    finally:
        copy.close()


def test_restore_never_resurrects_a_forgotten_source_domain(db, tmp_path):
    import sqlite3
    tid, out = consolidated(db, [two_sources(hosts=("embarrassing-clinic.example", "b.example"))])
    result, dest = _restore(db, tmp_path,
                            lambda: governance.forget_source(db, "embarrassing-clinic.example", T0 + 2))
    assert result["web_removed"]["sources"] == 1
    copy = sqlite3.connect(dest)
    try:
        assert copy.execute("SELECT count(*) FROM web_claims WHERE domain LIKE '%embarrassing%'").fetchone()[0] == 0
        assert copy.execute("SELECT status FROM web_knowledge").fetchall() == [("stale",)]
    finally:
        copy.close()
    assert b"embarrassing-clinic" not in raw_bytes(dest)


def test_deletion_log_keeps_only_a_digest_of_a_forgotten_source(db):
    """A-P1-2: neither deletion_log.target_id nor its effects name the forgotten domain or URL; the cache keys come
    back in the return value only."""
    _, out = consolidated(db, [two_sources(hosts=("embarrassing-clinic.example", "b.example"))])
    with store.write(db):
        state.save_source(db, out["task_id"], {"url": "http://embarrassing-clinic.example/numpy",
                                               "canonical_url": "http://embarrassing-clinic.example/numpy",
                                               "domain": "embarrassing-clinic.example", "retrieved_at": T0,
                                               "content_sha256": "cd" * 32}, T0)
    gone = governance.forget_source(db, "embarrassing-clinic.example", T0 + 1)
    assert gone["effects"]["cache"]["urls"] == ["http://embarrassing-clinic.example/numpy"]
    url = governance.forget_source(db, "http://b.example/numpy", T0 + 2)
    assert url["effects"]["cache"]["urls"]
    rows = db.execute("SELECT target_kind, target_id, effects FROM deletion_log").fetchall()
    assert [r[0] for r in rows] == ["source", "source"]
    assert rows[0][1].startswith("source_domain:#") and rows[1][1].startswith("source_url:#")
    for _, target, effects in rows:
        assert "example" not in target and "example" not in effects and "cache" not in json.loads(effects)
    assert b"embarrassing-clinic" not in raw_bytes(db_path(db))
    kinds = {k for (k,) in db.execute("SELECT kind FROM web_forgotten")}
    assert {"source_domain", "source_url", "knowledge"} <= kinds


def test_forgetting_the_middle_of_a_version_chain_relinks_instead_of_reviving(db):
    """C-P1-5: v1 -> v2 -> v3; forgetting v2 makes v1 point at v3 (still superseded), never a second head."""
    texts = ["NumPy 2.3.5 is the latest release of the NumPy library.", "NumPy 2.4.0 is the latest release of NumPy.",
             "NumPy 2.5.0 is the latest release of NumPy."]
    tasks, ids = [], []
    for n, text in enumerate(texts):
        tid, out = consolidated(db, [two_sources(published=T0 + n * DAY, text=text,
                                                 hosts=(f"h{n}a.example", f"h{n}b.example"))], T0 + (n + 1) * DAY)
        tasks.append(tid)
        ids.append(out["provisional"][0])
    gone = governance.forget_research(db, tasks[1], T0 + 9 * DAY)
    assert gone["effects"]["knowledge_deleted"] == [ids[1]] and gone["effects"]["versions_relinked"] == [ids[0]]
    rows = {r[0]: r[1:] for r in db.execute("SELECT id, status, superseded_by FROM web_knowledge")}
    assert rows[ids[0]] == ("superseded", ids[2]) and rows[ids[2]][1] is None
    heads = [k for k, (status, sup) in rows.items() if sup is None]
    assert heads == [ids[2]]


def test_forget_source_by_the_original_redirecting_url_removes_its_claims(db):
    """C-P1-6: a page reached through a redirect is forgotten by the URL the owner knows, claims included."""
    from neuroscifi.webcortex.extract import canonical_url
    final = "https://www.long.example/article?id=7"
    tid, _ = consolidated(db, [two_sources()])
    with store.write(db):
        sid = state.save_source(db, tid, {"url": "https://t.co/abc", "final_url": final,
                                          "canonical_url": canonical_url(final), "domain": "long.example",
                                          "retrieved_at": T0}, T0)
        db.execute("INSERT INTO web_claims(id,task_id,source_id,text,key,kind,polarity,source_url,canonical_url,domain,"
                   "retrieved_at,created_at) VALUES('c-redirect',?,?,'SECRET CLAIM TEXT','k','statement',1,?,?,"
                   "'long.example',?,?)", (tid, sid, canonical_url(final), canonical_url(final), T0, T0))
    gone = governance.forget_source(db, "https://t.co/abc", T0 + 1)
    assert gone["effects"]["sources_deleted"] == 1 and gone["effects"]["claims_removed"] == 1
    assert db.execute("SELECT count(*) FROM web_claims WHERE id='c-redirect'").fetchone()[0] == 0
    assert b"SECRET CLAIM TEXT" not in raw_bytes(db_path(db))


def test_forget_partner_scrubs_web_text_and_a_restored_copy(db, tmp_path):
    """A-P2-1 (ADR-019): a partner root in an owner-authorised task objective, its events, claims and knowledge is
    replaced by its label, live and in a restored checkpoint."""
    import sqlite3
    from neuroscifi.instance import checkpoints
    from neuroscifi.webcortex import orchestrator
    evidence.start_epoch(db, "e1", T0)
    evidence.observe(db, {"id": "w1", "epoch": "e1", "channel": "word", "action": 0, "bit": 1, "root": "zelphinaqq",
                          "sample_id": None, "actor": "owner", "consent": "learn", "provenance": "observed"}, T0 + 1)
    task = orchestrator.submit(db, "Who is zelphinaqq?", T0 + 2, mode="quick_lookup", origin="cli",
                               authorization=orchestrator.AUTHORIZED_DISCLOSURE)
    with store.write(db):
        state.log_event(db, task["id"], "query", {"text": "zelphinaqq biography"}, T0 + 2)
    cp = checkpoints.create(db, db_path(db), tmp_path / "cps", T0 + 3)
    governance.forget_partner(db, "zelphinaqq", T0 + 4)
    objective = db.execute("SELECT objective FROM web_tasks").fetchone()[0]
    assert "zelphinaqq" not in objective and "partner:#" in objective
    assert b"zelphinaqq" not in raw_bytes(db_path(db))
    dest = tmp_path / "restored.sqlite"
    checkpoints.restore(db, cp["id"], dest, T0 + 5)
    copy = sqlite3.connect(dest)
    try:
        assert "partner:#" in copy.execute("SELECT objective FROM web_tasks").fetchone()[0]
    finally:
        copy.close()
    assert b"zelphinaqq" not in raw_bytes(dest)


def test_forget_research_returns_unshared_cache_keys_and_the_cli_drops_them(tmp_path, capsys):
    """A-P2-4: `nsf forget research` drops the task's pages from the public cache unless another task read them."""
    from neuroscifi.cli import main
    from neuroscifi.webcortex.cache import PublicCache
    from neuroscifi.webcortex.services import cache_path
    path = tmp_path / "individual.sqlite"
    db = store.create(path, T0)
    state.set_config(db, {"enabled": True}, T0)
    first, _ = consolidated(db, [two_sources()])
    second, _ = consolidated(db, [two_sources(hosts=("c.example", "d.example"))], T0 + 1)
    with store.write(db):
        for tid, url in ((first, "http://only.example/a"), (first, "http://shared.example/b"),
                         (second, "http://shared.example/b")):
            state.save_source(db, tid, {"url": url, "canonical_url": url, "domain": url.split("/")[2],
                                        "retrieved_at": T0}, T0)
    db.close()
    from neuroscifi.webcortex.types import FetchResult
    cache = PublicCache(cache_path(path))
    for url in ("http://only.example/a", "http://shared.example/b"):
        cache.put(FetchResult(url=url, final_url=url, status=200, headers={}, content_type="text/html",
                              body=url.encode(), retrieved_at=T0))
    cache.close()
    assert main(["forget", "research", first, "--db", str(path)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["effects"]["cache"]["urls"] == ["http://only.example/a"]
    assert out["public_cache"]["entries_removed"] >= 1
    cache = PublicCache(cache_path(path))
    try:
        assert cache.lookup("http://only.example/a") is None and cache.lookup("http://shared.example/b") is not None
    finally:
        cache.close()


# ---- UAT RC1 regressions (lane L2) --------------------------------------------------------------------------------

def test_forget_knowledge_forgets_its_unfinished_refresh_tasks(db):
    """C-08: an owner refresh is recorded as 'refresh-owner:<id>' (shown "Refresh of knowledge #<id>", not
    autonomous); forgetting the item forgets its queued/running refreshes (their objective is the forgotten
    statement) - also a refresh queued before this origin existed (cli, same statement, parent = item's task) - and
    the result says to rebuild stale checkpoints (S-11)."""
    from neuroscifi.webcortex import orchestrator
    tid, out = consolidated(db, [two_sources()])
    kid = out["provisional"][0]
    statement = knowledge.row(db, kid)["statement"]
    owner = knowledge.refresh(db, kid, T0 + 1, origin="console")
    assert owner["origin"] == f"refresh-owner:{kid}" and owner["origin_label"] == f"Refresh of knowledge #{kid}"
    legacy = orchestrator.prepare(db, statement, T0 + 2, mode="fact_verification", origin="cli", parent=tid)
    with store.write(db):
        legacy_id = orchestrator.insert(db, legacy, T0 + 2)
        state.update_task(db, owner["id"], T0 + 2, status="reading", lease_holder="elsewhere:1:1",
                          lease_expires_at=T0 + 600)
    finished = orchestrator.submit(db, "An unrelated question about pandas releases?", T0 + 3,
                                   mode="fact_verification")
    checkpoint_row(db)
    gone = governance.forget_knowledge(db, kid, T0 + 4)
    assert sorted(gone["effects"]["refresh_tasks_forgotten"]) == sorted([owner["id"], legacy_id])
    assert db.execute("SELECT count(*) FROM web_tasks WHERE objective=?", (statement,)).fetchone()[0] == 0
    assert db.execute("SELECT 1 FROM web_tasks WHERE id=?", (finished["id"],)).fetchone() is not None
    assert gone["next_steps"] and "nsf checkpoint rebuild" in gone["next_steps"][0]
