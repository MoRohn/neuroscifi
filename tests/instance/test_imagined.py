"""The imagined store (ADR-049, schema 17): quarantined content that is never evidence, bounded, forgotten with what
it derives from, carried by checkpoints with its kinds and never brought back by a restore.

Each safeguard here has a test that fails when it is removed (scripts/genesis_provenance_mutations.py breaks them one
at a time). Web Cortex paths (retrieval, the answer bundle, the guard, web forgets) are in
tests/webcortex/test_imagined_quarantine.py."""
from contextlib import closing
import importlib.util
import json
from pathlib import Path
import sqlite3
import subprocess
import threading

import pytest

from neuroscifi.cli import main
from neuroscifi.instance import checkpoints, datasets, evidence, governance, graphs, imagined, schema, store
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
    number = _migration()       # later migrations (ADR-055) follow it; the file below migrates through all of them
    path = tmp_path / "old.sqlite"
    with closing(sqlite3.connect(path, isolation_level=None)) as raw:
        for version, script in enumerate(schema.MIGRATIONS[:number], start=1):
            raw.executescript("BEGIN;" + script + f";PRAGMA user_version={version};COMMIT;")
        raw.execute("INSERT INTO epochs(name,created_at) VALUES('e1',1)")
        raw.execute("INSERT INTO evidence(id,epoch,channel,action,bit,root,recorded_at) "
                    "VALUES('a','e1','word',0,1,'r',1)")
        before = raw.execute("SELECT * FROM evidence").fetchall()
        schema.migrate(raw)
        assert raw.execute("PRAGMA user_version").fetchone()[0] == schema.SCHEMA_VERSION > number
        assert raw.execute("SELECT * FROM evidence").fetchall() == before
        assert raw.execute("SELECT count(*) FROM imagined").fetchone()[0] == 0


# What the first, unreleased build of migration 17 left (review0 M2/L1/L4, M1): reference triggers that tested free
# text and missed upper case, padding, escapes and seven reference columns, a never-rewritten trigger that also froze
# the expiry, no insert triggers, no imagined_refs.observed, no tombstone age index.
_FIRST_17_JSON = "'*\"" + schema._IMAGINED_ID + "\"*'"
_FIRST_BUILD_17 = (
    "BEGIN; DROP TRIGGER imagined_never_rewritten; DROP TRIGGER imagined_lifetime_only_shortened; "
    "DROP TRIGGER imagined_never_replaced; DROP TRIGGER imagined_refs_never_replaced; "
    "DROP INDEX imagined_forgotten_age; ALTER TABLE imagined_refs DROP COLUMN observed; "
    "CREATE TRIGGER imagined_never_rewritten BEFORE UPDATE OF id, kind, payload, assumptions, uncertainty, mechanism, "
    "mechanism_version, model, question, created_at, expires_at ON imagined "
    "BEGIN SELECT RAISE(ABORT,'an imagined record is never rewritten'); END; "
    + "".join(f"DROP TRIGGER {t}_no_imagined_i; DROP TRIGGER {t}_no_imagined_u; "
              for t in ("web_tasks", "web_events", "web_mentions", "voice_segments", "web_knowledge"))
    + "CREATE TRIGGER web_knowledge_no_imagined_i BEFORE INSERT ON web_knowledge WHEN coalesce(NEW.sources,'') GLOB "
    + _FIRST_17_JSON + " BEGIN SELECT RAISE(ABORT,'an imagined record is never evidence'); END; "
    "CREATE TRIGGER web_knowledge_no_imagined_u BEFORE UPDATE OF task_id, supporting, contradicting, sources, "
    "verification ON web_knowledge WHEN coalesce(NEW.sources,'') GLOB " + _FIRST_17_JSON
    + " BEGIN SELECT RAISE(ABORT,'an imagined record is never evidence'); END; "
    "CREATE TRIGGER web_metrics_no_imagined_i BEFORE INSERT ON web_metrics WHEN coalesce(NEW.value,'') GLOB "
    + _FIRST_17_JSON + " BEGIN SELECT RAISE(ABORT,'an imagined record is never evidence'); END; COMMIT;")


def _all_triggers(db):
    return {name: " ".join(sql.split()) for name, sql in db.execute("SELECT name, sql FROM sqlite_master "
                                                                     "WHERE type='trigger'")}


OBSERVED_FIRST = "imagined:" + "fedcba9876543210" * 2


def _observed_as_the_first_build_did(db):
    """A record observed under the first build: free text in the outcome, its evidence outside imagined_refs."""
    db.execute("INSERT INTO imagined(id,kind,payload,assumptions,uncertainty,mechanism,mechanism_version,question,"
               "created_at,expires_at,outcome,observed_at) VALUES(?,'speculation','{\"value\": 1}','[]','u','m','1',"
               "'q',?,?,?,?)", (OBSERVED_FIRST, T0, T0 + DAY, json.dumps(
                   {"observed": {"note": "alice-secret-name said yes"}, "evidence": [["evidence", "ev-gone"]]}), T0))
    db.execute("INSERT INTO imagined_refs(record_id,ref_kind,ref_id) VALUES(?,'evidence','ev1')", (OBSERVED_FIRST,))


def test_a_file_of_the_first_build_of_migration_17_gets_todays_store_and_triggers(tmp_path):
    path = tmp_path / "first17.sqlite"
    db = store.create(path, T0)
    add(db, "ev1")
    rid = make(db, 1, derived_from=[("evidence", "ev1")])
    _observed_as_the_first_build_did(db)
    make(db, 2, derived_from=[("imagined", OBSERVED_FIRST)])
    db.close()
    with closing(sqlite3.connect(path, isolation_level=None)) as raw:
        raw.executescript(_FIRST_BUILD_17)
        assert "observed" not in [c[1] for c in raw.execute("PRAGMA table_info(imagined_refs)")]
    db = store.open_existing(path)
    try:
        # review0b N3: what the first build observed goes with the repair (and what derives from it), a tombstone each
        assert db.execute("SELECT id FROM imagined").fetchall() == [(rid,)] and reasons(db) == ["forgotten", "purged"]
        assert json.loads(db.execute("SELECT effects FROM deletion_log WHERE target_kind='imagined'").fetchone()[0])[
            "imagined_removed"] == 2
        fresh = store.create(tmp_path / "fresh.sqlite", T0)
        try:
            assert _all_triggers(db) == _all_triggers(fresh)
            assert schema.ensure_triggers(db) == []                    # once repaired, an open writes nothing
            index = "SELECT name FROM sqlite_master WHERE name='imagined_forgotten_age'"
            assert db.execute(index).fetchall() == fresh.execute(index).fetchall() != []
        finally:
            fresh.close()
        assert db.execute("SELECT record_id, ref_kind, ref_id, observed FROM imagined_refs").fetchall() == [
            (rid, "evidence", "ev1", 0)]
        with pytest.raises(sqlite3.IntegrityError, match="never rewritten"):
            db.execute("INSERT OR REPLACE INTO imagined SELECT * FROM imagined")
        db.execute("INSERT INTO web_metrics(kind,value,computed_at) VALUES('calibration',?,1)", (json.dumps([IMG]),))
        imagined.observe(db, rid, observation={"value": 2}, evidence=[("evidence", "ev1")], now=T0 + 1)
        assert governance.forget_evidence(db, "ev1", T0 + 2)["effects"]["imagined_removed"] == 1
    finally:
        db.close()


def test_two_processes_opening_a_first_build_file_together_both_open_it(tmp_path):
    """review0b N2: the catalogue was read before the write lock and not again, so the second of two processes
    opening a first-build file at once re-ran the repair and failed (duplicate column name: observed)."""
    path = tmp_path / "first17.sqlite"
    store.create(path, T0).close()
    with closing(sqlite3.connect(path, isolation_level=None)) as raw:
        raw.executescript(_FIRST_BUILD_17)
    gate, reached, second = threading.Event(), threading.Event(), {}

    class Waiting(sqlite3.Connection):        # has read the catalogue; waits before taking the write lock
        def execute(self, sql, *args):
            if sql.startswith("BEGIN IMMEDIATE"):
                reached.set()
                gate.wait(10)
            return super().execute(sql, *args)

    def open_second():
        with closing(sqlite3.connect(path, factory=Waiting, timeout=10, autocommit=False)) as b:
            try:
                second["changed"] = schema.ensure_triggers(b)
            except sqlite3.Error as error:
                second["changed"] = error
    thread = threading.Thread(target=open_second)
    thread.start()
    try:
        assert reached.wait(10)
        with closing(sqlite3.connect(path, timeout=10, autocommit=False)) as a:
            assert "imagined_refs.observed" in schema.ensure_triggers(a)
    finally:
        gate.set()
        thread.join(10)
    assert second["changed"] == []


def test_the_repair_drops_only_the_triggers_it_retired(instance):
    """review0b N6: every *_no_imagined_i/_u trigger not in today's list was dropped at every repair, so a later
    migration's trigger of that name would have been dropped too."""
    db, _ = instance
    db.execute("CREATE TRIGGER web_later_no_imagined_i BEFORE INSERT ON web_metrics BEGIN SELECT 1; END")
    db.execute("DROP TRIGGER web_tasks_no_imagined_i")                 # something for the repair to do
    assert schema.ensure_triggers(db) == ["web_tasks_no_imagined_i"]
    assert "web_later_no_imagined_i" in _all_triggers(db)


REPO = Path(__file__).resolve().parents[2]


def _schema_of(commit, tmp_path):
    """The schema module of an earlier, unreleased build, from the repository's history."""
    try:
        done = subprocess.run(["git", "show", f"{commit}:src/neuroscifi/instance/schema.py"], cwd=REPO,
                              capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        done = None
    if done is None or done.returncode != 0:
        pytest.skip(f"needs commit {commit} of the repository's history (a shallow clone lacks it)")
    path = tmp_path / f"schema_{commit}.py"
    path.write_text(done.stdout)
    spec = importlib.util.spec_from_file_location(f"neuroscifi.instance.schema_{commit}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _catalogue(db):
    out = {}
    for kind, name, sql in db.execute("SELECT type, name, sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"):
        out[kind, name] = (db.execute(f"PRAGMA table_xinfo({name})").fetchall() if kind == "table"
                           else " ".join((sql or "").split()))
    return out


# The two earlier, unreleased builds of migration 17: the first (2aee516) and the one after the review0 fixes
# (03cbece), each built from its own schema.py; the last case is a first-build file that 03cbece opened (and repaired
# without removing what the first build observed) before today's build opens it.
@pytest.mark.parametrize("builds", [("2aee516",), ("03cbece",), ("2aee516", "03cbece")],
                         ids=["first", "second", "first-then-second"])
def test_a_file_of_either_earlier_build_of_migration_17_is_repaired_to_todays(tmp_path, monkeypatch, builds):
    old = [_schema_of(commit, tmp_path) for commit in builds]
    path = tmp_path / "old17.sqlite"
    with monkeypatch.context() as m:
        m.setattr(store, "migrate", old[0].migrate)
        db = store.create(path, T0)
    add(db, "ev1")
    add(db, "ev2", root="carol")
    kept = make(db, 1, derived_from=[("evidence", "ev1")])
    if builds[0] == "2aee516":
        _observed_as_the_first_build_did(db)
        gone = [OBSERVED_FIRST, make(db, 2, derived_from=[("imagined", OBSERVED_FIRST)])]
    else:
        observed = make(db, 3, derived_from=[("evidence", "ev1")])
        imagined.observe(db, observed, observation={"value": 2}, evidence=[("evidence", "ev2")], now=T0 + 1)
        gone = []
    db.close()
    for later in old[1:]:
        with closing(sqlite3.connect(path, isolation_level=None)) as raw:
            later.ensure_triggers(raw)
            assert raw.execute("SELECT count(*) FROM imagined WHERE id=?", (OBSERVED_FIRST,)).fetchone()[0] == 1
    db = store.open_existing(path)
    try:
        fresh = store.create(tmp_path / "fresh.sqlite", T0)
        try:
            assert _catalogue(db) == _catalogue(fresh)
        finally:
            fresh.close()
        assert schema.ensure_triggers(db) == []
        held = {i for (i,) in db.execute("SELECT id FROM imagined")}
        assert kept in held and not held & set(gone) and len(held) == (1 if gone else 2)
        assert reasons(db) == (["forgotten", "purged"] if gone else [])
        assert governance.forget_evidence(db, "ev2", T0 + 2)["effects"].get("imagined_removed", 0) == (0 if gone else 1)
    finally:
        db.close()


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
    out = imagined.observe(db, rid, observation={"value": 1}, evidence=[("evidence", "seen")], now=T0 + 5)
    assert out["outcome"] == {"observed": {"value": 1}, "evidence": [["evidence", "seen"]]}
    assert out["observed_at"] == T0 + 5 and out["kind"] == "speculation" and out["label"] == "imagined, not evidence"
    with pytest.raises(sqlite3.IntegrityError, match="once"):
        imagined.observe(db, rid, observation={"value": 0}, now=T0 + 6)
    other = make(db, 2)
    with pytest.raises(ValidationError):          # an observation is never another imagined record
        imagined.observe(db, other, observation=1, evidence=[("imagined", rid)], now=T0)
    with pytest.raises(ValidationError):          # nor something the file does not hold
        imagined.observe(db, other, observation=1, evidence=[("evidence", "never")], now=T0)


def test_an_insert_or_replace_never_rewrites_a_record_or_its_lineage(instance):
    """review0 L1: REPLACE's implicit delete used to rewrite kind, payload and expiry and take the lineage with it."""
    db, _ = instance
    add(db, "seen")
    rid = make(db, derived_from=[("evidence", "seen")])
    before = db.execute("SELECT * FROM imagined").fetchall(), db.execute("SELECT * FROM imagined_refs").fetchall()
    with pytest.raises(sqlite3.IntegrityError, match="never rewritten"):
        db.execute("INSERT OR REPLACE INTO imagined(id,kind,payload,assumptions,uncertainty,mechanism,"
                   "mechanism_version,question,created_at,expires_at) VALUES(?,'inference','{}','[]','u','m','1','q',"
                   "?,?)", (rid, T0, T0 + 1e9))
    with pytest.raises(sqlite3.IntegrityError, match="never rewritten"):
        db.execute("INSERT OR REPLACE INTO imagined_refs(record_id,ref_kind,ref_id) VALUES(?,'evidence','seen')",
                   (rid,))
    after = db.execute("SELECT * FROM imagined").fetchall(), db.execute("SELECT * FROM imagined_refs").fetchall()
    assert after == before and count(db, "imagined_forgotten") == 0
    # the lifetime may be brought forward (a lower limit), never pushed back
    db.execute("UPDATE imagined SET expires_at=expires_at-1 WHERE id=?", (rid,))
    with pytest.raises(sqlite3.IntegrityError, match="never rewritten"):
        db.execute("UPDATE imagined SET expires_at=expires_at+10 WHERE id=?", (rid,))


def test_an_observation_carries_no_text_and_its_evidence_is_forgotten_with_the_record(instance):
    """review0 M1: the outcome's evidence was outside imagined_refs and its free text survived forget_partner."""
    db, _ = instance
    add(db, "ev-alice", root="alice-secret-name")
    add(db, "ev-bob", root="bob")
    rid = make(db, 1, derived_from=[("partner", "bob")])
    for text in ({"note": "alice-secret-name said yes"}, "alice-secret-name", {"value": "alice"}, [1, 2],
                 {"value": float("nan")}, {"alice-secret-name": 1}, {"value": {"name": 1}}):
        with pytest.raises(ValidationError):
            imagined.observe(db, rid, observation=text, evidence=[("partner", "alice-secret-name")], now=T0 + 1)
    out = imagined.observe(db, rid, observation={"value": 3, "scenario": None}, now=T0 + 2,
                           evidence=[("evidence", "ev-alice"), ("partner", "alice-secret-name"), ("partner", "bob")])
    assert out["derived_from"] == [["partner", "bob"]]                 # the lineage stays what it was derived from
    assert out["outcome"]["evidence"] == [["evidence", "ev-alice"], ["partner", "alice-secret-name"],
                                          ["partner", "bob"]]
    assert db.execute("SELECT ref_kind, ref_id, observed FROM imagined_refs ORDER BY ref_kind, ref_id").fetchall() == [
        ("evidence", "ev-alice", 1), ("partner", "alice-secret-name", 1), ("partner", "bob", 0)]
    effects = governance.forget_partner(db, "alice-secret-name", T0 + 3)["effects"]
    assert effects["imagined_removed"] == 1 and count(db) == 0 and count(db, "imagined_refs") == 0


def test_an_observation_is_refused_while_writes_are_paused(instance, monkeypatch):
    db, _ = instance
    rid = make(db, 1)
    monkeypatch.setattr(store, "write_allowed", lambda db, path=None: (False, "paused for the test"))
    with pytest.raises(store.WritesPaused):
        imagined.observe(db, rid, observation=1, now=T0 + 1)
    assert imagined.show(db, rid)["outcome"] is None


def test_an_observed_number_is_bounded_so_it_carries_no_text(instance):
    """review0b N4: any finite integer was taken, so text fitted in its digits (a name and an address decoded from
    one), and 10**400 raised OverflowError instead of ValidationError."""
    db, _ = instance
    rid = make(db, 1)
    secret = int.from_bytes(b"alice-secret-name, 12 Elm St", "big")
    for value in (secret, -secret, 10 ** 400, imagined.MAX_INT + 1, -imagined.MAX_INT - 1, float("inf")):
        for observation in (value, {"value": value}):
            with pytest.raises(ValidationError):
                imagined.observe(db, rid, observation=observation, now=T0 + 1)
    assert imagined.show(db, rid)["outcome"] is None
    out = imagined.observe(db, rid, observation={"value": imagined.MAX_INT, "scenario": -1.5e300}, now=T0 + 2)
    assert out["outcome"]["observed"] == {"value": 2 ** 53, "scenario": -1.5e300}


def test_an_event_or_epoch_holding_an_imagined_id_is_refused_before_the_database_sees_it(instance):
    """review0b N10: the belief revision's cause ("evidence:<id>") is checked in any case, so an event id such as
    "Imagined:<32 hex>-retry" was refused with a raw IntegrityError, as was an epoch named like one at its forget."""
    db, _ = instance
    evidence.start_epoch(db, "e1", T0)
    before = count(db, "evidence"), count(db, "belief_revisions"), count(db, "epochs")
    plain = {"id": "ev-x", "epoch": "e1", "channel": "word", "action": 0, "bit": 1, "root": "bob"}
    for field, value in (("id", "Imagined:" + HEX.upper() + "-retry"), ("id", f"retry {IMG}"), ("root", IMG.upper()),
                         ("root", f" {IMG} "), ("sample_id", f"s-{IMG}")):
        with pytest.raises(ValidationError, match="imagined"):
            evidence.observe(db, plain | {field: value}, T0 + 1)
    for name in (f"run {IMG}", "IMAGINED:" + HEX.upper()):
        with pytest.raises(ValidationError, match="imagined"):
            evidence.start_epoch(db, name, T0)
    assert (count(db, "evidence"), count(db, "belief_revisions"), count(db, "epochs")) == before
    assert evidence.observe(db, plain | {"id": "Reimagined: a book"}, T0 + 1)["status"] == "created"


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
# "whole": the column holds one id; "inside": a revision cause that names one; "list": a JSON list of ids;
# "objects": a JSON list of objects that name a claim and a task (and quote free text beside them).
REFERENCE_COLUMNS = {
    "evidence": {"id": "whole", "root": "whole", "sample_id": "whole"},
    "evidence_consolidated": {"root": "whole", "sample_id": "whole"},
    "belief_revisions": {"cause": "inside"},
    "web_tasks": {"parent_id": "whole"},
    "web_events": {"task_id": "whole"},
    "web_sources": {"task_id": "whole"},
    "web_claims": {"id": "whole", "task_id": "whole", "source_id": "whole"},
    "web_claim_links": {"src": "whole", "dst": "whole", "task_id": "whole"},
    "web_knowledge": {"task_id": "whole", "supporting": "list", "contradicting": "objects"},
    "web_mentions": {"claim_id": "whole", "knowledge_id": "whole", "task_id": "whole"},
    "web_hypotheses": {"task_id": "whole", "knowledge_id": "whole", "parent_id": "whole", "claims": "list"},
    "web_predictions": {"task_id": "whole", "knowledge_id": "whole", "hypothesis_id": "whole"},
    "web_feedback": {"task_id": "whole", "knowledge_id": "whole"},
    "voice_segments": {"task_id": "whole"},
    "web_monitors": {"last_task_id": "whole"},             # review0b N8
}
HEX = IMG.split(":")[1]
# Each form an id may take in such a column (review0 L4: case, padding and a JSON escape used to hide one).
VALUES = {"whole": [IMG, IMG.upper(), f" {IMG}", f"{IMG}\n", f"\t{IMG} "],
          "inside": [f"evidence:{IMG}", f"evidence:{IMG.upper()}"],
          "list": [json.dumps([IMG]), f'[ "{IMG}" ]', json.dumps(["c1", IMG.upper()]),
                   '["\\u0069magined:' + HEX + '"]']}
VALUES["objects"] = VALUES["list"] + [json.dumps([{"claim": IMG, "text": "q"}]),
                                      json.dumps([{"claim": "c1", "task": f" {IMG}"}]),
                                      json.dumps([{"claim_id": IMG.upper()}])]               # review0b N8
BENIGN = {"whole": "Reimagined: a book", "inside": "evidence:imagined:not-an-id",
          "list": json.dumps(["Reimagined: x", "c1"]),
          "objects": json.dumps([{"claim": "c1", "task": "t1", "text": IMG, "url": f"http://a/{IMG}"}])}


def _seed_rows(db):
    add(db, "ev1")
    db.execute("UPDATE evidence SET sample_id='s1'")
    db.execute("INSERT INTO evidence_consolidated(seq,epoch,channel,root,value,sample_id,consolidated_at) "
               "VALUES(99,'e1','word','r',1,'s9',1)")
    db.execute("INSERT INTO belief_revisions(epoch,state_version,created_at,cause,summary) VALUES('e1',1,1,'c','{}')")
    db.execute("INSERT INTO web_tasks(id,objective,mode,status,origin,created_at,updated_at) "
               "VALUES('t1','o','quick_lookup','completed','test',1,1)")
    db.execute("INSERT INTO web_events(task_id,kind,created_at) VALUES('t1','claim',1)")
    db.execute("INSERT INTO web_sources(task_id,url,canonical_url,domain,kind,retrieved_at,retrieval_mode,created_at) "
               "VALUES('t1','http://a/x','http://a/x','a','page',1,'live',1)")
    db.execute("INSERT INTO web_claims(id,task_id,text,key,kind,polarity,source_url,domain,retrieved_at,created_at) "
               "VALUES('c1','t1','x','k','statement',1,'http://a/x','a',1,1)")
    db.execute("INSERT INTO web_claim_links(task_id,src,dst,kind,created_at) VALUES('t1','c1','c1','supports',1)")
    db.execute("INSERT INTO web_knowledge(key,version,statement,status,confidence,uncertainty,freshness_class,"
               "first_verified_at,last_verified_at,created_at,updated_at) "
               "VALUES('k',1,'s','provisional',0.8,0.1,'stable',1,1,1,1)")
    db.execute("INSERT INTO web_entities(name,normalized,kind,first_seen,last_seen) VALUES('n','n','name',1,1)")
    db.execute("INSERT INTO web_mentions(entity_id,claim_id,knowledge_id,task_id,created_at) VALUES(1,'c1',1,'t1',1)")
    db.execute("INSERT INTO web_hypotheses(key,statement,origin,status,created_at,updated_at) "
               "VALUES('k','s','gap','open',1,1)")
    db.execute("INSERT INTO web_predictions(knowledge_id,expectation,check_after,created_at) VALUES(1,'e',2,1)")
    db.execute("INSERT INTO web_feedback(key,effect,created_at) VALUES('k','new',1)")
    db.execute("INSERT INTO web_metrics(kind,computed_at) VALUES('calibration',1)")
    db.execute("INSERT INTO web_monitors(topic,mode,interval_seconds,authorized_by,status,created_at,updated_at) "
               "VALUES('t','continuous',3600,'owner','active',1,1)")
    db.execute("INSERT INTO voice_sessions(id,principal,device,mode,status,started_at,updated_at) "
               "VALUES('v','p','d','text_only','active',1,1)")
    db.execute("INSERT INTO voice_segments(session_id,utterance,seq,speaker,source,status,authority,text,started_at,"
               "created_at,updated_at) VALUES('v','u',1,'user','typed','final','user_typed','t',1,1,1)")


CASES = [(t, c, f, n) for t, cols in REFERENCE_COLUMNS.items() for c, f in cols.items() for n in range(len(VALUES[f]))]
# a second row of these tables needs its own key: the trigger, not the key, must be what refuses the copy
_UNIQUE = {"web_claims": "task_id", "web_sources": "canonical_url", "voice_segments": "utterance"}


@pytest.mark.parametrize("table,column,form,n", CASES, ids=[f"{t}.{c}-{n}" for t, c, _, n in CASES])
def test_no_evidence_side_column_may_name_an_imagined_record(instance, table, column, form, n):
    db, _ = instance
    _seed_rows(db)
    value = VALUES[form][n]
    rowid = db.execute(f"SELECT rowid FROM {table} LIMIT 1").fetchone()[0]
    with pytest.raises(sqlite3.IntegrityError, match="never evidence"):
        db.execute(f"UPDATE {table} SET {column}=? WHERE rowid=?", (value, rowid))
    names = [r[1] for r in db.execute(f"PRAGMA table_info({table})") if r[1] not in ("id", "seq") or r[1] == column]
    picks = ", ".join("?" if c == column else f"{c}||'2'" if c == _UNIQUE.get(table) else c for c in names)
    with pytest.raises(sqlite3.IntegrityError, match="never evidence"):
        db.execute(f"INSERT INTO {table}({','.join(names)}) SELECT {picks} FROM {table} WHERE rowid=?",
                   (value, rowid))
    db.execute(f"UPDATE {table} SET {column}=? WHERE rowid=?", (BENIGN[form], rowid))     # no false alarm


# Free text that a web page or a template writes (review0 M2: a page titled like an id used to abort a whole
# consolidation): never tested, whatever it holds.
FREE_TEXT = {"web_knowledge": ("sources", "verification", "statement", "note", "claim_values"),
             "web_claims": ("text", "title", "source_url", "claim_values", "flags"),
             "web_sources": ("title", "url", "metadata"),
             "web_claim_links": ("detail",), "web_predictions": ("detail", "criteria", "expectation"),
             "web_feedback": ("detail",), "web_metrics": ("value",), "web_events": ("detail",),
             "web_hypotheses": ("verification", "statement", "alternatives"),
             "belief_revisions": ("summary",), "voice_segments": ("text", "detail")}


@pytest.mark.parametrize("table", sorted(FREE_TEXT))
def test_free_text_may_read_like_an_imagined_id(instance, table):
    db, _ = instance
    _seed_rows(db)
    rowid = db.execute(f"SELECT rowid FROM {table} LIMIT 1").fetchone()[0]
    for column in FREE_TEXT[table]:
        for value in (IMG, json.dumps([{"url": "https://evil.example/p", "title": IMG}]), json.dumps({"claim": IMG})):
            db.execute(f"UPDATE {table} SET {column}=? WHERE rowid=?", (value, rowid))


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


def test_a_lower_limit_applies_to_the_records_already_held(instance, tmp_path):
    """review0 L3: lowering max_imagined_rows or imagined_lifetime_seconds used to leave held records as they were."""
    db, path = instance
    ids = [make(db, n, now=T0 + n) for n in range(4)]
    out = store.set_config(db, {"max_imagined_rows": 2}, T0 + 10)
    assert out["imagined_removed"] == 2 and [i["id"] for i in imagined.listing(db, T0 + 10)["items"]] == ids[:1:-1]
    assert reasons(db) == ["evicted", "evicted"]
    out = store.set_config(db, {"imagined_lifetime_seconds": 60}, T0 + 20)      # expiry brought forward
    assert "imagined_removed" not in out
    assert [imagined.show(db, i)["expires_at"] for i in ids[2:]] == [T0 + 62, T0 + 63]
    assert imagined.listing(db, T0 + 62.5)["items"][0]["id"] == ids[3]
    assert governance.sweep(db, T0 + 3600)["imagined_expired"] == 2 and count(db) == 0
    store.set_config(db, {"imagined_lifetime_seconds": 7 * DAY}, T0 + 3601)      # never pushed back
    rid = make(db, 9, now=T0 + 3601)
    assert store.set_config(db, {"imagined_lifetime_seconds": 60}, T0 + 3700)["imagined_removed"] == 1
    assert count(db) == 0                    # past the new lifetime at once: removed with the change
    assert ("imagined", "expired") in db.execute("SELECT target_kind, target_id FROM deletion_log").fetchall()
    with pytest.raises(NotFound):
        imagined.show(db, rid)
    assert store.set_config(db, {"max_imagined_rows": 0}, T0 + 3800)["changed"] == ["max_imagined_rows"]


def test_the_sweep_removes_a_record_whose_source_went_any_other_way(instance):
    """review0 L3: the sweep looked for orphans only when an imagined record itself expired."""
    db, _ = instance
    add(db, "ev1")
    rid = make(db, 1, derived_from=[("evidence", "ev1")])
    kept = make(db, 2)
    with store.write(db):
        db.execute("DELETE FROM evidence WHERE id='ev1'")       # gone without a forget (a path that does not cascade)
    assert imagined.show(db, rid)
    out = governance.sweep(db, T0 + 1)
    assert out["imagined_expired"] == 1 and [i["id"] for i in imagined.listing(db, T0 + 2)["items"]] == [kept]
    assert reasons(db) == ["forgotten"]


def test_tombstones_are_kept_as_long_as_a_record_can_live_and_no_longer(instance, tmp_path):
    """review0 note: imagined_forgotten grew without bound under eviction churn."""
    db, path = instance
    store.set_config(db, {"imagined_lifetime_seconds": store.MAX_IMAGINED_LIFETIME}, T0)
    rid = make(db, 1)
    cp = checkpoints.create(db, path, tmp_path / "cps", T0 + 1)
    imagined.purge(db, T0 + 2, record_id=rid)
    governance.sweep(db, T0 + 2 + store.MAX_IMAGINED_LIFETIME - 1)
    assert count(db, "imagined_forgotten") == 1
    governance.sweep(db, T0 + 2 + store.MAX_IMAGINED_LIFETIME)
    assert count(db, "imagined_forgotten") == 0
    # an archive still holding the record cannot bring it back: by now it has expired there too
    checkpoints.restore(db, cp["id"], tmp_path / "restored.sqlite", T0 + 3 + store.MAX_IMAGINED_LIFETIME)
    assert _records(tmp_path / "restored.sqlite") == []


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
    # nothing new derives from an epoch once it was forgotten (review0 L3), even when it has evidence again
    add(db, "w3", epoch_name="e1", root="ann")
    with pytest.raises(ValidationError, match="does not hold"):
        make(db, 4, now=T0 + 10, derived_from=[("epoch", "e1")])
    other = make(db, 5, now=T0 + 10)
    with pytest.raises(ValidationError, match="does not hold"):
        imagined.observe(db, other, observation=1, evidence=[("epoch", "e1")], now=T0 + 11)


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
