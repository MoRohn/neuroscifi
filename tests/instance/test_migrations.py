"""Forward-only schema migrations: version-1 to -5 files reach the current schema with their data intact.

Old files are built from the old migration strings themselves (schema.MIGRATIONS[:n]) and filled with the rows of
a real individual, so the data is what the software actually writes. Migration 3 folds beliefs.DDL (the
changepoints table and the evidence_tally index) into the schema; migration 4 adds epoch_links ("continue there");
migration 5 adds epoch_contexts and epoch_streams (panel dynamics, decision D-1); migration 6 adds the twelve
Web Cortex tables (ADR-029).
"""
import re
import sqlite3

import numpy as np
import pytest

from neuroscifi.instance import beliefs, evidence, executive, governance, schema, store
from conftest import T0


def _source(tmp_path):
    """A current individual with evidence in two epochs, decisions, questions, revisions and a forgotten partner."""
    path = tmp_path / "source.sqlite"
    db = store.create(path, T0)
    for epoch in ("desk", "lab"):
        evidence.start_epoch(db, epoch, T0)
        for i in range(6):
            for channel, root, corrected in (("camera", f"cam-{epoch}", 0), ("word", "ana", 1),
                                             ("reference", f"ref-{i % 3}", 0)):
                n = len(epoch) * 100 + i * 3 + ("camera", "word", "reference").index(channel)
                evidence.observe(db, {"id": f"{epoch}-{n}", "epoch": epoch, "channel": channel, "action": i % 2,
                                      "bit": corrected ^ (i % 2), "root": root}, T0 + n)
        for i, bit in enumerate((0, 1, 0, 1)):  # an ambiguous partner, so the executive opens a question
            evidence.observe(db, {"id": f"{epoch}-cara-{i}", "epoch": epoch, "channel": "word", "action": 0,
                                  "bit": bit, "root": "cara"}, T0 + 900 + i)
        assert executive.decide(db, epoch, T0 + 1000, record=True)["scope"] == "partner:cara"
    evidence.observe(db, {"id": "gone", "epoch": "desk", "channel": "word", "action": 0, "bit": 0,
                          "root": "Bob-x"}, T0 + 1001)
    governance.forget_partner(db, "Bob-x", T0 + 1002)
    db.close()
    return path


def _old_file(tmp_path, source, version):
    """A file at `version`, created by MIGRATIONS[:version] and filled with the source's rows of those tables."""
    path = tmp_path / f"v{version}.sqlite"
    raw = sqlite3.connect(path, isolation_level=None)
    for number, script in enumerate(schema.MIGRATIONS[:version], start=1):
        raw.executescript("BEGIN;" + script + f";PRAGMA user_version={number};COMMIT;")
    tables = [name for (name,) in raw.execute("SELECT name FROM sqlite_master WHERE type='table' "
                                              "AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    raw.execute("ATTACH DATABASE ? AS src", (str(source),))
    for table in tables:
        raw.execute(f"INSERT INTO main.{table} SELECT * FROM src.{table}")
    raw.execute("DETACH DATABASE src")
    raw.close()
    return path


def _dump(db, tables):
    return {table: db.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall() for table in tables}


def _tables(db):
    return [name for (name,) in db.execute("SELECT name FROM sqlite_master WHERE type='table' "
                                           "AND name NOT LIKE 'sqlite_%' ORDER BY name")]


def _objects(db):
    return db.execute("SELECT type, name, sql FROM sqlite_master ORDER BY type, name").fetchall()


def _statements(text):
    """SQL statements of a script, comments dropped and whitespace normalized."""
    text = re.sub(r"--[^\n]*", "", text)
    return [" ".join(part.split()) for part in text.split(";") if part.strip()]


def test_migration_3_is_exactly_the_beliefs_ddl():
    assert _statements(schema.MIGRATIONS[2]) == [" ".join(s.split()) for s in beliefs.DDL]


def test_migration_4_adds_epoch_links():
    assert _statements(schema.MIGRATIONS[3]) == [
        "CREATE TABLE epoch_links( epoch TEXT PRIMARY KEY, source_epoch TEXT NOT NULL, segments TEXT NOT NULL, "
        "pending_id INTEGER, created_at REAL NOT NULL)"]


def test_migration_5_adds_epoch_contexts_and_streams():
    assert schema.SCHEMA_VERSION == 6
    assert _statements(schema.MIGRATIONS[4]) == [
        "CREATE TABLE epoch_contexts( epoch TEXT PRIMARY KEY REFERENCES epochs(name), context TEXT NOT NULL, "
        "created_at REAL NOT NULL)",
        "CREATE TABLE epoch_streams( id INTEGER PRIMARY KEY, epoch TEXT NOT NULL REFERENCES epochs(name), "
        "blob BLOB NOT NULL, rows INTEGER, cols INTEGER, sha256 TEXT NOT NULL, "
        "provenance TEXT NOT NULL CHECK(provenance IN ('observed','simulated')), created_at REAL NOT NULL, "
        "expires_at REAL)"]


@pytest.mark.parametrize("version", [1, 2, 3, 4, 5])
def test_old_file_migrates_to_current_with_data_intact(tmp_path, version):
    source = _source(tmp_path)
    path = _old_file(tmp_path, source, version)
    with sqlite3.connect(path) as raw:
        tables = _tables(raw)
        before = _dump(raw, tables)
        assert raw.execute("PRAGMA user_version").fetchone()[0] == version
        assert ("changepoints" in tables) == (version >= 3) and ("epoch_links" in tables) == (version >= 4)
        assert ("epoch_contexts" in tables) == (version >= 5) and ("epoch_streams" in tables) == (version >= 5)
        assert not any(t.startswith("web_") for t in tables)
    assert before["evidence"] and before["belief_revisions"] and before["decisions"] and before["pending"]
    if version == 2:
        assert len(before["forgotten_roots"]) == 1

    db = store.open_existing(path)
    try:
        assert db.execute("PRAGMA user_version").fetchone()[0] == schema.SCHEMA_VERSION
        assert _dump(db, tables) == before
        assert db.execute("SELECT count(*) FROM changepoints").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM epoch_links").fetchone()[0] == 0
        assert [row[1] for row in db.execute("PRAGMA table_info(epoch_links)")] == [
            "epoch", "source_epoch", "segments", "pending_id", "created_at"]
        for table in ("epoch_contexts", "epoch_streams"):   # migration 5 adds both, empty
            assert db.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0
        web = [t for t in _tables(db) if t.startswith("web_")]   # migration 6 adds the Web Cortex tables, empty
        assert len(web) == 12 and all(db.execute(f"SELECT count(*) FROM {t}").fetchone()[0] == 0 for t in web)
        assert db.execute("SELECT 1 FROM sqlite_master WHERE type='index' AND name='evidence_tally'").fetchone()
        if version == 1:  # migration 2 adds forgotten_roots, empty
            assert db.execute("SELECT count(*) FROM forgotten_roots").fetchone()[0] == 0
        assert db.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []

        # The migrated individual keeps its beliefs and works with the current software.
        with sqlite3.connect(source) as raw:
            expected = {epoch: beliefs.compute(raw, epoch) for epoch in ("desk", "lab")}
        for epoch in ("desk", "lab"):
            assert beliefs.compute(db, epoch) == expected[epoch]
        objects = _objects(db)
        with store.write(db):
            beliefs.ensure_tables(db)  # idempotent no-op on a migrated file
        assert _objects(db) == objects
        evidence.observe(db, {"id": "after", "epoch": "desk", "channel": "camera", "action": 0, "bit": 0,
                              "root": "cam-desk"}, T0 + 2000)
        assert executive.decide(db, "desk", T0 + 2001, record=True)["decision"] in ("act", "ask", "test_reference")
        # The upgraded individual can continue an earlier setup: a new epoch from desk's mapping links to desk.
        evidence.start_epoch(db, "return", T0 + 3000)
        for i in range(4):
            for channel, root, corrected in (("camera", "cam-desk", 0), ("word", "ana", 1),
                                             ("reference", f"ref-{i % 3}", 0)):
                evidence.observe(db, {"id": f"return-{channel}-{i}", "epoch": "return", "channel": channel,
                                      "action": 0, "bit": corrected, "root": root}, T0 + 3001 + i)
        # desk and lab share Ana and the references, so neither reaches 3:1; the owner answers a desk question.
        with store.write(db):
            question = db.execute("INSERT INTO pending(epoch,kind,scope,question,created_at) VALUES('return','ask',"
                                  "'epoch:desk','This looks like desk; continue with that calibration?',?)",
                                  (T0 + 3100,)).lastrowid
        done = executive.resolve(db, question, "continue there", T0 + 3101, confirm=True)
        assert done["link"]["source_epoch"] == "desk"
        assert beliefs.compute(db, "return")["linked_from"]["epoch"] == "desk"
        # ... and can declare a panel context and attach a population stream (migration 5).
        assert evidence.set_epoch_context(db, "return", "panel", T0 + 3200)["changed"] is True
        rng = np.random.default_rng(0)
        attached = evidence.attach_stream(db, "return", rng.normal(size=(12, 3)), rng.normal(size=(12, 3)),
                                          T0 + 3201)
        assert beliefs.match_epochs(db, "return")["stream"]["id"] == attached["id"]
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        db.close()


def test_version_2_file_with_lazily_created_changepoints_migrates(tmp_path):
    """Software between migrations 2 and 3 created the table lazily (beliefs.ensure_tables); its rows survive."""
    source = _source(tmp_path)
    path = _old_file(tmp_path, source, 2)
    with sqlite3.connect(path) as raw:
        for statement in beliefs.DDL:
            raw.execute(statement)
        raw.execute("INSERT INTO changepoints(epoch,scope,since_seq,pending_id,created_at,confirmed) "
                    "VALUES('desk','camera:cam-desk',5,NULL,?,1)", (T0 + 3000,))
        before = _dump(raw, _tables(raw))
    db = store.open_existing(path)
    try:
        assert db.execute("PRAGMA user_version").fetchone()[0] == schema.SCHEMA_VERSION
        assert _dump(db, list(before)) == before
        assert beliefs.changes(db, "desk")["confirmed"] == {"camera:cam-desk": [5]}
    finally:
        db.close()


def test_opening_a_current_file_is_a_no_op(tmp_path):
    path = tmp_path / "current.sqlite"
    store.create(path, T0).close()
    with sqlite3.connect(path) as raw:
        cookie = raw.execute("PRAGMA schema_version").fetchone()[0]
        objects = _objects(raw)
        before = _dump(raw, _tables(raw))
    db = store.connect(path)
    try:
        assert schema.migrate(db) == schema.SCHEMA_VERSION == 6
        assert db.total_changes == 0
        assert db.execute("PRAGMA schema_version").fetchone()[0] == cookie
        assert _objects(db) == objects and _dump(db, list(before)) == before
    finally:
        db.close()
    store.open_existing(path).close()
    with sqlite3.connect(path) as raw:
        assert raw.execute("PRAGMA user_version").fetchone()[0] == 6
        assert raw.execute("PRAGMA schema_version").fetchone()[0] == cookie


def test_newer_file_is_refused(tmp_path):
    path = tmp_path / "future.sqlite"
    store.create(path, T0).close()
    with sqlite3.connect(path) as raw:
        raw.execute(f"PRAGMA user_version={schema.SCHEMA_VERSION + 1}")
    with pytest.raises(Exception, match="newer than this software"):
        store.open_existing(path)
