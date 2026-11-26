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
    assert schema.SCHEMA_VERSION >= 8
    assert _statements(schema.MIGRATIONS[4]) == [
        "CREATE TABLE epoch_contexts( epoch TEXT PRIMARY KEY REFERENCES epochs(name), context TEXT NOT NULL, "
        "created_at REAL NOT NULL)",
        "CREATE TABLE epoch_streams( id INTEGER PRIMARY KEY, epoch TEXT NOT NULL REFERENCES epochs(name), "
        "blob BLOB NOT NULL, rows INTEGER, cols INTEGER, sha256 TEXT NOT NULL, "
        "provenance TEXT NOT NULL CHECK(provenance IN ('observed','simulated')), created_at REAL NOT NULL, "
        "expires_at REAL)"]


def test_migration_7_adds_web_tombstones_and_a_claim_time_index():
    assert _statements(schema.MIGRATIONS[6]) == [
        "CREATE TABLE web_forgotten( kind TEXT NOT NULL CHECK(kind IN ('task','knowledge','source_url',"
        "'source_domain')), digest TEXT NOT NULL, forgotten_at REAL NOT NULL, PRIMARY KEY(kind, digest))",
        "CREATE INDEX web_claims_created ON web_claims(created_at)"]


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
        web = [t for t in _tables(db) if t.startswith("web_")]   # migrations 6-8 add the Web Cortex tables, empty
        assert len(web) == 15 + 4          # lane K's migration adds 4 (ADR-055)
        assert all(db.execute(f"SELECT count(*) FROM {t}").fetchone()[0] == 0 for t in web)
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
        assert schema.migrate(db) == schema.SCHEMA_VERSION >= 8
        assert db.total_changes == 0
        assert db.execute("PRAGMA schema_version").fetchone()[0] == cookie
        assert _objects(db) == objects and _dump(db, list(before)) == before
    finally:
        db.close()
    store.open_existing(path).close()
    with sqlite3.connect(path) as raw:
        assert raw.execute("PRAGMA user_version").fetchone()[0] == schema.SCHEMA_VERSION
        assert raw.execute("PRAGMA schema_version").fetchone()[0] == cookie


def test_newer_file_is_refused(tmp_path):
    path = tmp_path / "future.sqlite"
    store.create(path, T0).close()
    with sqlite3.connect(path) as raw:
        raw.execute(f"PRAGMA user_version={schema.SCHEMA_VERSION + 1}")
    with pytest.raises(Exception, match="newer than this software"):
        store.open_existing(path)


class _UpgradedMeanwhile(sqlite3.Connection):
    """A connection that lets another process finish the whole upgrade right after it first reads user_version:
    the interleaving of two processes opening an old file at once, made deterministic (no threads, no sleeps)."""
    other = None

    def execute(self, sql, *args):
        result = super().execute(sql, *args)
        if sql.strip() == "PRAGMA user_version" and self.other is not None:
            other, self.other = self.other, None
            version = result.fetchone()
            schema.migrate(other)
            return super().execute("SELECT ?", version)     # what this process read before the other upgraded
        return result


def test_a_second_process_opening_the_file_during_an_upgrade_does_not_reapply_a_migration(tmp_path):
    """The version is re-read under the write lock, so the slower opener sees the upgrade done and applies nothing
    (it used to re-run migration 1 and fail with 'already exists', left inside its transaction)."""
    path = tmp_path / "old.sqlite"
    first = sqlite3.connect(path, isolation_level=None, timeout=1)
    second = sqlite3.connect(path, isolation_level=None, timeout=1, factory=_UpgradedMeanwhile)
    try:
        second.other = first
        assert schema.migrate(second) == schema.SCHEMA_VERSION
        assert second.other is None and not second.in_transaction and not first.in_transaction
        assert second.execute("PRAGMA user_version").fetchone()[0] == schema.SCHEMA_VERSION
        assert second.execute("PRAGMA integrity_check").fetchone() == ("ok",)
    finally:
        first.close()
        second.close()


def test_a_failing_migration_is_rolled_back_and_leaves_no_open_transaction(tmp_path, monkeypatch):
    broken = list(schema.MIGRATIONS)
    broken[0] = "CREATE TABLE meta(key TEXT); CREATE TABLE meta(key TEXT);"
    monkeypatch.setattr(schema, "MIGRATIONS", broken)
    db = sqlite3.connect(tmp_path / "new.sqlite", isolation_level=None)
    try:
        with pytest.raises(sqlite3.OperationalError, match="already exists"):
            schema.migrate(db)
        assert not db.in_transaction
        assert db.execute("SELECT count(*) FROM sqlite_master").fetchone()[0] == 0
        assert db.execute("PRAGMA user_version").fetchone()[0] == 0
    finally:
        db.close()


# ---- migration 11: voice tombstones get their own kind -------------------------------------------------------------

def _v10_file(tmp_path):
    """A schema-10 individual whose web_forgotten holds a research-task, a knowledge and a voice-session tombstone,
    written as software before migration 11 wrote them (the voice one as kind 'task', subject "voice:<id>")."""
    source = tmp_path / "source.sqlite"
    store.create(source, T0).close()
    path = tmp_path / "v10.sqlite"
    raw = sqlite3.connect(path, isolation_level=None)
    for number, script in enumerate(schema.MIGRATIONS[:10], start=1):
        raw.executescript("BEGIN;" + script + f";PRAGMA user_version={number};COMMIT;")
    raw.execute("ATTACH DATABASE ? AS src", (str(source),))
    for table in ("meta", "counters", "scoring_rules"):
        raw.execute(f"INSERT INTO main.{table} SELECT * FROM src.{table}")
    raw.execute("DETACH DATABASE src")
    instance_id = store.birth(raw)["instance_id"]
    voice = governance.voice_digest(instance_id, "s-1")
    task = governance.web_digest(instance_id, "task", "3f0c1a52-0000-4000-8000-000000000001")
    know = governance.web_digest(instance_id, "knowledge", "4:numpy latest version")
    raw.executemany("INSERT INTO web_forgotten(kind,digest,forgotten_at) VALUES(?,?,?)",
                    [("task", task, T0 + 1), ("knowledge", know, T0 + 2), ("task", voice, T0 + 3)])
    raw.executemany("INSERT INTO deletion_log(target_kind,target_id,requested_at,effects) VALUES(?,?,?,'{}')",
                    [("web_task", "3f0c1a52-0000-4000-8000-000000000001", T0 + 1),
                     ("web_knowledge", "4", T0 + 2), ("voice_session", "voice_session:#" + voice, T0 + 3)])
    raw.close()
    return path, {"task": task, "knowledge": know, "voice": voice}


def test_migration_11_relabels_voice_tombstones_and_keeps_every_other_row(tmp_path):
    assert schema.SCHEMA_VERSION >= 11 and governance.VOICE_KIND == "voice_session"
    path, d = _v10_file(tmp_path)
    with sqlite3.connect(path) as raw:
        with pytest.raises(sqlite3.IntegrityError):          # the v10 CHECK has no voice kind
            raw.execute("INSERT INTO web_forgotten VALUES('voice_session','x',1)")
    db = store.open_existing(path)
    try:
        assert db.execute("PRAGMA user_version").fetchone()[0] == schema.SCHEMA_VERSION
        assert sorted(db.execute("SELECT kind,digest,forgotten_at FROM web_forgotten").fetchall()) == sorted([
            ("task", d["task"], T0 + 1), ("knowledge", d["knowledge"], T0 + 2),
            ("voice_session", d["voice"], T0 + 3)])
        assert db.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        with store.write(db):                               # the new kind is accepted, unknown kinds still refused
            db.execute("INSERT INTO web_forgotten VALUES('voice_session','y',1)")
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("INSERT INTO web_forgotten VALUES('epoch','z',1)")
        changes = db.total_changes
        assert schema.migrate(db) == schema.SCHEMA_VERSION and db.total_changes == changes   # a second run: no-op
    finally:
        db.close()


def test_migration_11_is_atomic_and_replays_after_an_interruption(tmp_path, monkeypatch):
    path, d = _v10_file(tmp_path)
    broken = list(schema.MIGRATIONS)
    broken[10] = schema.MIGRATIONS[10] + "\nCREATE TABLE web_forgotten(x);"   # fails after every other statement
    monkeypatch.setattr(schema, "MIGRATIONS", broken)
    raw = sqlite3.connect(path, isolation_level=None)
    with pytest.raises(sqlite3.OperationalError):
        schema.migrate(raw)
    raw.close()
    with sqlite3.connect(path) as raw:
        assert raw.execute("PRAGMA user_version").fetchone()[0] == 10
        assert sorted(raw.execute("SELECT kind FROM web_forgotten")) == [("knowledge",), ("task",), ("task",)]
        assert "web_forgotten_v11" not in {n for (n,) in raw.execute("SELECT name FROM sqlite_master")}
    monkeypatch.undo()
    db = store.open_existing(path)
    try:
        assert db.execute("SELECT kind FROM web_forgotten WHERE digest=?", (d["voice"],)).fetchone() == \
            ("voice_session",)
    finally:
        db.close()


# ---- migration 13: retired node keys; sessions held by nodes enrolled before signing are released ------------------

def test_migration_13_releases_sessions_of_unsigned_nodes(tmp_path):
    """Security review A-6: a node enrolled before signing (signed=0) that owned a session, or was offered one, when
    version 12 arrived kept it; version 13 releases both, and keeps a signed node's session."""
    path = tmp_path / "v12.sqlite"
    store.create(path, T0).close()
    raw = sqlite3.connect(path, isolation_level=None)
    raw.executescript("BEGIN; DROP TABLE forgotten_scopes; DROP TRIGGER graph_erased_stays_invalidated; "
                      "DROP TRIGGER graph_revisions_never_deleted; "
                      "DROP TABLE evidence_consolidated; DROP INDEX tombstones_epoch_seq; "
                      "DROP TABLE node_keys_retired; "
                      "PRAGMA user_version=12; COMMIT;")
    raw.executemany("INSERT INTO autonomy_nodes(device,family,label,capabilities,enrolled_by,enrolled_at,alg,"
                    "public_key,key_fingerprint,signed) VALUES(?,?,?,'[\"node\"]','owner',?,?,?,?,?)",
                    [("legacy", "fam", "old", T0, None, None, None, 0),
                     ("signed", "fam", "new", T0, "Ed25519", "A" * 43, "F" * 39, 1)])
    raw.executemany("INSERT INTO autonomy_sessions(session,family,owner,acquired_at,expires_at,offered_to,"
                    "offer_expires_at) VALUES(?,?,?,?,?,?,?)",
                    [("s1", "fam", "legacy", T0, T0 + 900, None, None),
                     ("s2", "fam", "signed", T0, T0 + 900, "legacy", T0 + 300)])
    raw.close()
    db = store.open_existing(path)
    try:
        assert db.execute("PRAGMA user_version").fetchone()[0] == schema.SCHEMA_VERSION >= 13
        assert db.execute("SELECT session, owner, offered_to FROM autonomy_sessions").fetchall() == \
            [("s2", "signed", None)]
        assert db.execute("SELECT count(*) FROM node_keys_retired").fetchone()[0] == 0
    finally:
        db.close()
