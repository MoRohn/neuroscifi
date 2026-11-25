"""The imagined store (ADR-049, schema 17): quarantined content that is never evidence, bounded, forgotten with what
it derives from, carried by checkpoints with its kinds and never brought back by a restore.

Each safeguard here has a test that fails when it is removed (scripts/genesis_provenance_mutations.py breaks them one
at a time). Web Cortex paths (retrieval, the answer bundle, the guard, web forgets) are in
tests/webcortex/test_imagined_quarantine.py."""
from contextlib import closing
import json
import sqlite3

import pytest

from neuroscifi.cli import main
from neuroscifi.instance import checkpoints, datasets, governance, graphs, imagined, schema, store
from neuroscifi.instance.errors import NotFound, ValidationError
from neuroscifi import provenance
from _seed import T0, add

DAY = 86400.0
IMG = "imagined:" + "0123456789abcdef" * 2          # the shape of an id, held by no record


def make(db, seed=1, now=T0, derived_from=(), **kw):
    return imagined.record(db, now=now, **(imagined.example(seed, derived_from=derived_from) | kw))


def count(db, table="imagined"):
    return db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]


def reasons(db):
    return sorted(r for (r,) in db.execute("SELECT reason FROM imagined_forgotten"))


# ---- the vocabulary -----------------------------------------------------------------------------------------------

def test_the_vocabulary_has_five_kinds_and_quarantines_three():
    assert provenance.KINDS == ("evidence", "inference", "prediction", "simulation", "speculation")
    assert provenance.QUARANTINED == {"inference", "simulation", "speculation"} == set(imagined.KINDS)
    kinds = {row["table"]: row["kind"] for row in provenance.mapping()}
    for table in ("evidence", "web_claims", "web_sources", "web_knowledge", "datasets", "evidence_consolidated"):
        assert kinds[table] == "evidence", table
    assert kinds["web_hypotheses"] == kinds["web_predictions"] == "prediction"
    # no existing table is a simulation: the legacy 'simulated' label is an observed simulated environment
    assert "simulation" not in {k for t, k in kinds.items() if t != "imagined"}
    assert "never SIMULATION" in provenance.__doc__


# ---- schema 17 ----------------------------------------------------------------------------------------------------

def _migration():
    return next(i for i, script in enumerate(schema.MIGRATIONS) if "CREATE TABLE IF NOT EXISTS imagined(" in script)


def test_the_migration_is_the_last_and_changes_no_row_of_a_version_16_file(tmp_path):
    number = _migration()
    assert number == len(schema.MIGRATIONS) - 1
    path = tmp_path / "old.sqlite"
    with closing(sqlite3.connect(path, isolation_level=None)) as raw:
        for version, script in enumerate(schema.MIGRATIONS[:number], start=1):
            raw.executescript("BEGIN;" + script + f";PRAGMA user_version={version};COMMIT;")
        raw.execute("INSERT INTO epochs(name,created_at) VALUES('e1',1)")
        raw.execute("INSERT INTO evidence(id,epoch,channel,action,bit,root,recorded_at) "
                    "VALUES('a','e1','word',0,1,'r',1)")
        before = raw.execute("SELECT * FROM evidence").fetchall()
        schema.migrate(raw)
        assert raw.execute("PRAGMA user_version").fetchone()[0] == number + 1 == schema.SCHEMA_VERSION
        assert raw.execute("SELECT * FROM evidence").fetchall() == before
        assert raw.execute("SELECT count(*) FROM imagined").fetchone()[0] == 0


def test_a_new_individual_has_an_empty_store(instance):
    db, _ = instance
    assert imagined.present(db) and count(db) == count(db, "imagined_refs") == count(db, "imagined_forgotten") == 0
    assert imagined.listing(db, T0) == {"label": "imagined, not evidence",
                                        "counts": {"simulation": 0, "speculation": 0, "inference": 0}, "items": []}


def test_the_database_takes_no_other_kind(instance):
    db, _ = instance
    for kind in ("evidence", "prediction", "observed", "fact"):
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("INSERT INTO imagined(id,kind,payload,assumptions,uncertainty,mechanism,mechanism_version,"
                       "question,created_at,expires_at) VALUES(?,?,'{}','[]','u','m','1','q',1,2)", (IMG, kind))
        with pytest.raises(ValidationError):
            make(db, kind=kind)


def test_a_record_is_never_rewritten_and_its_outcome_is_filled_once(instance):
    db, _ = instance
    add(db, "seen")
    rid = make(db, derived_from=[("evidence", "seen")])
    for column, value in (("kind", "inference"), ("payload", "{}"), ("assumptions", "[]"), ("uncertainty", "sure"),
                          ("mechanism", "other"), ("question", "q"), ("created_at", 0), ("expires_at", 9e9),
                          ("id", IMG), ("model", "m")):
        with pytest.raises(sqlite3.IntegrityError, match="never rewritten"):
            db.execute(f"UPDATE imagined SET {column}=? WHERE id=?", (value, rid))
    with pytest.raises(sqlite3.IntegrityError, match="never rewritten"):
        db.execute("UPDATE imagined_refs SET ref_id='other' WHERE record_id=?", (rid,))
    out = imagined.observe(db, rid, observation={"reading": 1}, evidence=[("evidence", "seen")], now=T0 + 5)
    assert out["outcome"] == {"observed": {"reading": 1}, "evidence": [["evidence", "seen"]]}
    assert out["observed_at"] == T0 + 5 and out["kind"] == "speculation" and out["label"] == "imagined, not evidence"
    with pytest.raises(sqlite3.IntegrityError, match="once"):
        imagined.observe(db, rid, observation={"reading": 0}, now=T0 + 6)
    other = make(db, 2)
    with pytest.raises(ValidationError):          # an observation is never another imagined record
        imagined.observe(db, other, observation="x", evidence=[("imagined", rid)], now=T0)
    with pytest.raises(ValidationError):          # nor something the file does not hold
        imagined.observe(db, other, observation="x", evidence=[("evidence", "never")], now=T0)


def test_a_record_names_only_sources_the_file_holds(instance):
    db, _ = instance
    for ref in (("evidence", "nope"), ("task", "t"), ("knowledge", 9), ("epoch", "e9"), ("partner", "nobody"),
                ("dataset", 4), ("graph", 7), ("imagined", IMG)):
        with pytest.raises(ValidationError, match="does not hold"):
            make(db, derived_from=[ref])
    with pytest.raises(ValidationError):
        make(db, derived_from=[("belief", "x")])
    assert count(db) == 0


# ---- nothing on the evidence side may name an imagined record ------------------------------------------------------

# The reference columns, written out here (not read from schema) so that dropping one from the schema fails a test.
REFERENCE_COLUMNS = {
    "evidence": {"id": "whole", "root": "whole", "sample_id": "whole"},
    "evidence_consolidated": {"root": "whole", "sample_id": "whole"},
    "belief_revisions": {"cause": "inside"},
    "web_sources": {"task_id": "whole"},
    "web_claims": {"id": "whole", "task_id": "whole"},
    "web_claim_links": {"src": "whole", "dst": "whole", "task_id": "whole", "detail": "json"},
    "web_knowledge": {"task_id": "whole", "supporting": "json", "contradicting": "json", "sources": "json",
                      "verification": "json"},
    "web_hypotheses": {"task_id": "whole", "claims": "json"},
    "web_predictions": {"task_id": "whole", "detail": "json"},
    "web_feedback": {"task_id": "whole", "detail": "json"},
    "web_metrics": {"value": "json"},
}
VALUE = {"whole": IMG, "inside": f"evidence:{IMG}", "json": json.dumps({"claim": IMG, "list": [IMG]})}
BENIGN = {"whole": "Reimagined: a book", "inside": "evidence:imagined:not-an-id", "json": '["Reimagined: x"]'}


def _seed_rows(db):
    add(db, "ev1")
    db.execute("UPDATE evidence SET sample_id='s1'")
    db.execute("INSERT INTO evidence_consolidated(seq,epoch,channel,root,value,sample_id,consolidated_at) "
               "VALUES(99,'e1','word','r',1,'s9',1)")
    db.execute("INSERT INTO belief_revisions(epoch,state_version,created_at,cause,summary) VALUES('e1',1,1,'c','{}')")
    db.execute("INSERT INTO web_tasks(id,objective,mode,status,origin,created_at,updated_at) "
               "VALUES('t1','o','quick_lookup','completed','test',1,1)")
    db.execute("INSERT INTO web_sources(task_id,url,canonical_url,domain,kind,retrieved_at,retrieval_mode,created_at) "
               "VALUES('t1','http://a/x','http://a/x','a','page',1,'live',1)")
    db.execute("INSERT INTO web_claims(id,task_id,text,key,kind,polarity,source_url,domain,retrieved_at,created_at) "
               "VALUES('c1','t1','x','k','statement',1,'http://a/x','a',1,1)")
    db.execute("INSERT INTO web_claim_links(task_id,src,dst,kind,created_at) VALUES('t1','c1','c1','supports',1)")
    db.execute("INSERT INTO web_knowledge(key,version,statement,status,confidence,uncertainty,freshness_class,"
               "first_verified_at,last_verified_at,created_at,updated_at) "
               "VALUES('k',1,'s','provisional',0.8,0.1,'stable',1,1,1,1)")
    db.execute("INSERT INTO web_hypotheses(key,statement,origin,status,created_at,updated_at) "
               "VALUES('k','s','gap','open',1,1)")
    db.execute("INSERT INTO web_predictions(knowledge_id,expectation,check_after,created_at) VALUES(1,'e',2,1)")
    db.execute("INSERT INTO web_feedback(key,effect,created_at) VALUES('k','new',1)")
    db.execute("INSERT INTO web_metrics(kind,computed_at) VALUES('calibration',1)")


CASES = [(t, c, f) for t, cols in REFERENCE_COLUMNS.items() for c, f in cols.items()]


@pytest.mark.parametrize("table,column,form", CASES, ids=[f"{t}.{c}" for t, c, _ in CASES])
def test_no_evidence_side_column_may_name_an_imagined_record(instance, table, column, form):
    db, _ = instance
    _seed_rows(db)
    rowid = db.execute(f"SELECT rowid FROM {table} LIMIT 1").fetchone()[0]
    with pytest.raises(sqlite3.IntegrityError, match="never evidence"):
        db.execute(f"UPDATE {table} SET {column}=? WHERE rowid=?", (VALUE[form], rowid))
    names = [r[1] for r in db.execute(f"PRAGMA table_info({table})") if r[1] not in ("id", "seq") or r[1] == column]
    picks = ", ".join("?" if n == column else n for n in names)
    with pytest.raises(sqlite3.IntegrityError, match="never evidence"):
        db.execute(f"INSERT INTO {table}({','.join(names)}) SELECT {picks} FROM {table} WHERE rowid=?",
                   (VALUE[form], rowid))
    db.execute(f"UPDATE {table} SET {column}=? WHERE rowid=?", (BENIGN[form], rowid))     # no false alarm


def test_every_reference_column_has_its_triggers(instance):
    db, _ = instance
    triggers = {name for (name,) in db.execute("SELECT name FROM sqlite_master WHERE type='trigger'")}
    for table in REFERENCE_COLUMNS:
        assert {f"{table}_no_imagined_i", f"{table}_no_imagined_u"} <= triggers, table


# ---- bounded --------------------------------------------------------------------------------------------------------

def test_the_limit_evicts_the_oldest_and_zero_takes_nothing(instance):
    db, _ = instance
    store.set_config(db, {"max_imagined_rows": 3}, T0)
    ids = [make(db, n, now=T0 + n) for n in range(5)]
    assert count(db) == 3
    assert [i["id"] for i in imagined.listing(db, T0 + 10)["items"]] == ids[:1:-1]
    assert reasons(db) == ["evicted", "evicted"]
    assert db.execute("SELECT target_kind, target_id FROM deletion_log WHERE target_kind='imagined'").fetchall() == [
        ("imagined", "evicted"), ("imagined", "evicted")]
    store.set_config(db, {"max_imagined_rows": 0}, T0)
    with pytest.raises(ValidationError, match="takes nothing"):
        make(db, 9)
    with pytest.raises(ValidationError):
        store.set_config(db, {"max_imagined_rows": store.MAX_IMAGINED_ROWS + 1}, T0)


def test_a_record_expires_with_the_sweep_and_derived_records_go_with_it(instance):
    db, _ = instance
    store.set_config(db, {"imagined_lifetime_seconds": DAY}, T0)
    first = make(db, 1)
    second = make(db, 2, now=T0 + DAY / 2, derived_from=[("imagined", first)])
    short = make(db, 3, lifetime=60)
    assert imagined.show(db, short)["expires_at"] == T0 + 60
    assert imagined.show(db, first)["expires_at"] == T0 + DAY
    with pytest.raises(ValidationError):
        make(db, 4, lifetime=-1)
    assert [i["id"] for i in imagined.listing(db, T0 + 61)["items"]] == [second, first]    # past its life: not listed
    assert governance.sweep(db, T0 + 61)["imagined_expired"] == 1
    out = governance.sweep(db, T0 + DAY)
    assert out["imagined_expired"] == 2 and count(db) == 0      # the derived one goes with its source
    assert "imagined_expired" not in governance.sweep(db, T0 + 2 * DAY)
    assert reasons(db) == ["expired", "expired", "forgotten"]


def test_the_settings_are_checked_and_a_damaged_value_reads_as_the_default(instance):
    db, _ = instance
    for name, bad in (("max_imagined_rows", -1), ("max_imagined_rows", True), ("imagined_lifetime_seconds", 1),
                      ("imagined_lifetime_seconds", 400 * DAY)):
        with pytest.raises(ValidationError):
            store.set_config(db, {name: bad}, T0)
    raw = json.loads(store._meta(db, "config")) | {"max_imagined_rows": "lots", "imagined_lifetime_seconds": -5}
    with store.write(db):
        db.execute("UPDATE meta SET value=? WHERE key='config'", (json.dumps(raw),))
    cfg = store.config(db)
    assert cfg.max_imagined_rows == 1000 and cfg.imagined_lifetime_seconds == 7 * DAY
    assert len([p for p in store.setting_problems(db) if "imagined" in p]) == 2


# ---- forgetting covers it -----------------------------------------------------------------------------------------

def test_forgetting_an_event_removes_what_was_imagined_from_it_and_from_that(instance):
    db, _ = instance
    add(db, "ev1")
    add(db, "ev2")
    first = make(db, 1, derived_from=[("evidence", "ev1")])
    chained = make(db, 2, derived_from=[("imagined", first)])
    kept = make(db, 3, derived_from=[("evidence", "ev2")])
    effects = governance.forget_evidence(db, "ev1", T0 + 1)["effects"]
    assert effects["imagined_removed"] == 2
    assert [i["id"] for i in imagined.listing(db, T0 + 2)["items"]] == [kept]
    with pytest.raises(NotFound):
        imagined.show(db, chained)
    assert reasons(db) == ["forgotten", "forgotten"] and count(db, "imagined_refs") == 1
    log = json.loads(db.execute("SELECT effects FROM deletion_log WHERE target_kind='evidence'").fetchone()[0])
    assert log["imagined_removed"] == 2


def test_a_forget_with_nothing_imagined_reports_as_before(instance):
    db, _ = instance
    add(db, "ev1")
    make(db, 1)                                                   # derived from nothing: no forget reaches it
    assert "imagined_removed" not in governance.forget_evidence(db, "ev1", T0 + 1)["effects"]
    assert count(db) == 1


def test_forgetting_an_epoch_a_partner_or_a_camera_removes_what_derives_from_it(instance):
    db, _ = instance
    add(db, "w1", epoch_name="e1", root="ann")
    add(db, "c1", epoch_name="e2", channel="camera", root="cam")
    add(db, "w2", epoch_name="e2", root="bob")
    make(db, 1, derived_from=[("epoch", "e1")])
    make(db, 2, derived_from=[("partner", "bob")])
    make(db, 3, derived_from=[("camera", "cam")])
    assert governance.forget_epoch(db, "e1", T0 + 1)["effects"]["imagined_removed"] == 1
    assert governance.forget_partner(db, "bob", T0 + 2)["effects"]["imagined_removed"] == 1
    assert governance.forget_camera(db, "cam", T0 + 3)["effects"]["imagined_removed"] == 1
    assert count(db) == 0
    # an epoch forgotten before a record was made does not reach that record
    add(db, "w3", epoch_name="e1", root="ann")
    later = make(db, 4, now=T0 + 10, derived_from=[("epoch", "e1")])
    governance.forget_evidence(db, "w3", T0 + 11)
    assert imagined.show(db, later)["kind"] == imagined.KINDS[4 % 3]


def test_expiring_the_evidence_a_record_derives_from_removes_it(instance):
    db, _ = instance
    add(db, "ev1", expires_at=T0 + 10)
    make(db, 1, derived_from=[("evidence", "ev1")])
    governance.sweep(db, T0 + 11)
    assert count(db) == 0 and reasons(db) == ["forgotten"]


def test_forgetting_a_dataset_removes_what_derives_from_it_and_from_its_erased_graphs(instance):
    db, _ = instance
    ids = datasets.simulate(db, "ctx", T0, seed=1)
    rev = graphs.learn(db, "ctx", T0, train=ids["train"], validation=ids["validation"], holder="t")
    make(db, 1, derived_from=[("dataset", ids["train"])])
    make(db, 2, derived_from=[("graph", rev)])
    kept = make(db, 3, derived_from=[("dataset", ids["heldout"])])
    out = governance.forget_dataset(db, ids["train"], T0 + 1)
    assert out["effects"]["imagined_removed"] == 2
    assert [i["id"] for i in imagined.listing(db, T0 + 2)["items"]] == [kept]


def test_the_owner_purges_one_a_kind_or_everything(instance):
    db, _ = instance
    rid = make(db, 0)                       # simulation
    make(db, 1)                             # speculation
    make(db, 2)                             # inference
    make(db, 3)                             # simulation
    assert imagined.purge(db, T0 + 1, record_id=rid)["effects"]["imagined_removed"] == 1
    assert imagined.purge(db, T0 + 1, record_id=rid)["status"] == "absent"
    assert imagined.purge(db, T0 + 2, kind="simulation")["effects"]["imagined_removed"] == 1
    assert imagined.purge(db, T0 + 3)["effects"]["imagined_removed"] == 2
    assert count(db) == 0 and reasons(db) == ["purged"] * 4
    assert [t for (t,) in db.execute("SELECT target_id FROM deletion_log WHERE target_kind='imagined' ORDER BY id")] \
        == [rid, "simulation", "all"]
    with pytest.raises(ValidationError):
        imagined.purge(db, T0, record_id=rid, kind="simulation")


# ---- checkpoints carry it, restores never bring a removed record back ---------------------------------------------

def _records(path):
    with closing(sqlite3.connect(path)) as c:
        return c.execute("SELECT id, kind, payload FROM imagined ORDER BY id").fetchall()


def test_a_checkpoint_and_its_restore_carry_the_store_with_its_kinds(instance, tmp_path):
    db, path = instance
    for n in range(3):
        make(db, n)
    cp = checkpoints.create(db, path, tmp_path / "cps", T0 + 1)
    out = checkpoints.restore(db, cp["id"], tmp_path / "restored.sqlite", T0 + 2)
    assert out["imagined_removed"] == 0
    assert _records(tmp_path / "restored.sqlite") == _records(path)
    assert sorted(k for _, k, _ in _records(path)) == ["inference", "simulation", "speculation"]


@pytest.mark.parametrize("how", ["purge", "forget", "expire", "evict"])
def test_a_restore_never_brings_back_a_record_removed_since(instance, tmp_path, how):
    db, path = instance
    add(db, "ev1")
    store.set_config(db, {"max_imagined_rows": 2, "imagined_lifetime_seconds": DAY}, T0)
    gone = make(db, 1, derived_from=[("evidence", "ev1")])
    stays = make(db, 2, now=T0 + 1)
    cp = checkpoints.create(db, path, tmp_path / "cps", T0 + 2)
    if how == "purge":
        imagined.purge(db, T0 + 3, record_id=gone)
    elif how == "forget":
        governance.forget_evidence(db, "ev1", T0 + 3)
    elif how == "expire":
        governance.sweep(db, T0 + DAY + 0.5)                     # the first record's life ended, not the second's
    else:
        make(db, 3, now=T0 + 3)                                  # the limit evicts the oldest
    assert db.execute("SELECT status FROM checkpoints WHERE id=?", (cp["id"],)).fetchone()[0] == "stale"
    out = checkpoints.restore(db, cp["id"], tmp_path / "restored.sqlite", T0 + 4)
    assert out["imagined_removed"] == 1
    held = {i for i, _, _ in _records(tmp_path / "restored.sqlite")}
    assert gone not in held and stays in held
    with closing(sqlite3.connect(tmp_path / "restored.sqlite")) as copy:   # and the copy keeps the tombstones
        assert copy.execute("SELECT count(*) FROM imagined_forgotten").fetchone()[0] >= 1


def test_a_purge_rewrites_the_archives_it_made_stale(instance, tmp_path):
    db, path = instance
    rid = make(db, 1)
    cp = checkpoints.create(db, path, tmp_path / "cps", T0 + 1)
    imagined.purge(db, T0 + 2, record_id=rid)
    assert checkpoints.rewritable(db, checkpoints._row(db, cp["id"])) is None
    out = checkpoints.after_forget(db, T0 + 3)
    assert out["checkpoints_stale"] == [] and [r["id"] for r in out["checkpoints_rewritten"]] == [cp["id"]]


def test_an_archive_written_before_the_store_existed_still_restores(instance, tmp_path):
    db, path = instance
    cp = checkpoints.create(db, path, tmp_path / "cps", T0 + 1)
    assert checkpoints.restore(db, cp["id"], tmp_path / "r.sqlite", T0 + 2)["imagined_removed"] == 0


# ---- the owner's commands ------------------------------------------------------------------------------------------

def test_nsf_imagined_lists_shows_and_purges_with_plain_labels(instance, capsys):
    import time
    db, path = instance
    rid = make(db, 1, now=time.time())                  # the CLI sweeps at the real clock before every command
    make(db, 2, now=time.time())
    assert main(["imagined", "list", "--db", str(path)]) == 0
    listed = json.loads(capsys.readouterr().out)
    assert listed["label"] == "imagined, not evidence" and len(listed["items"]) == 2
    assert all(i["label"] == "imagined, not evidence" and i["kind"] in imagined.KINDS for i in listed["items"])
    assert main(["imagined", "show", rid, "--db", str(path)]) == 0
    shown = json.loads(capsys.readouterr().out)
    assert shown["kind"] == "speculation" and shown["label"] == "imagined, not evidence" and shown["assumptions"]
    assert main(["imagined", "purge", "--all", "--db", str(path)]) == 0
    assert json.loads(capsys.readouterr().out)["effects"]["imagined_removed"] == 2
    assert main(["imagined", "kinds"]) == 0
    kinds = json.loads(capsys.readouterr().out)
    assert kinds["never_evidence"] == ["inference", "simulation", "speculation"]
    with pytest.raises(SystemExit):
        main(["imagined", "purge", "--db", str(path)])           # a purge names what it removes
    assert main(["config", "--max-imagined-rows", "5", "--imagined-lifetime-days", "2", "--db", str(path)]) == 0
    cfg = json.loads(capsys.readouterr().out)["config"]
    assert cfg["max_imagined_rows"] == 5 and cfg["imagined_lifetime_seconds"] == 2 * DAY


def test_the_section_for_a_model_is_labelled_and_carries_no_id(instance):
    db, _ = instance
    rid = make(db, 1)
    part = imagined.section([imagined.show(db, rid)])
    assert part["label"] == "imagined, not evidence"
    assert rid not in json.dumps(part) and all("id" not in item for item in part["items"])


def test_the_generator_is_deterministic_and_writes_nothing(instance):
    db, _ = instance
    assert imagined.example(5) == imagined.example(5) != imagined.example(6)
    assert count(db) == 0
