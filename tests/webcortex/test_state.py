"""Migration 6 and state.py: tables, vocabularies, configuration, events, cluster persistence, procedural memory."""
import re
import sqlite3

import pytest

from neuroscifi.instance import schema, store
from neuroscifi.instance.errors import Conflict, ValidationError
from neuroscifi.webcortex import config as wconfig, state, types
from test_consolidate import T0, claim, cluster, task_with, open_db


@pytest.fixture
def db(tmp_path):
    yield from open_db(tmp_path)

WEB_TABLES = {"web_tasks", "web_events", "web_sources", "web_claims", "web_claim_links", "web_knowledge",
              "web_entities", "web_mentions", "web_strategies", "web_monitors", "web_predictions", "web_feedback"}


def _check_values(db, table, column):
    sql = db.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()[0]
    match = re.search(rf"{column} [A-Z ]*CHECK\({column} IN \(([^)]*)\)\)", " ".join(sql.split()))
    assert match, (table, column)
    return tuple(v.strip().strip("'") for v in match.group(1).split(","))


def test_fresh_individual_has_schema_6_and_every_web_table(db):
    assert schema.SCHEMA_VERSION == 6 and db.execute("PRAGMA user_version").fetchone()[0] == 6
    tables = {n for (n,) in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'web_%'")}
    assert tables == WEB_TABLES
    indexes = {n for (n,) in db.execute("SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'web_%'")}
    assert {"web_tasks_status", "web_claims_key", "web_knowledge_status", "web_monitors_due"} <= indexes


def test_check_constraints_use_the_types_vocabularies(db):
    assert _check_values(db, "web_tasks", "mode") == types.MODES
    assert _check_values(db, "web_tasks", "status") == types.TASK_STATES
    assert _check_values(db, "web_sources", "kind") == types.DOC_KINDS
    assert _check_values(db, "web_sources", "retrieval_mode") == types.RETRIEVAL_MODES
    assert _check_values(db, "web_claims", "kind") == types.CLAIM_KINDS
    assert _check_values(db, "web_knowledge", "freshness_class") == types.FRESHNESS_CLASSES
    assert _check_values(db, "web_knowledge", "status") == state.KNOWLEDGE_STATES
    assert _check_values(db, "web_events", "kind") == state.EVENT_KINDS
    assert _check_values(db, "web_monitors", "status") == state.MONITOR_STATES
    assert _check_values(db, "web_feedback", "effect") == state.FEEDBACK_EFFECTS
    assert _check_values(db, "web_claim_links", "kind") == state.LINK_KINDS
    with pytest.raises(sqlite3.IntegrityError):
        with store.write(db):
            db.execute("INSERT INTO web_tasks(id,objective,mode,status,origin,created_at,updated_at) "
                       "VALUES('x','q','quick_lookup','sleeping','cli',1,1)")


def test_version_5_file_migrates_and_reports_web_inventory(tmp_path):
    source = tmp_path / "source.sqlite"
    store.create(source, T0).close()
    old = tmp_path / "v5.sqlite"
    raw = sqlite3.connect(old, isolation_level=None)
    for number, script in enumerate(schema.MIGRATIONS[:5], start=1):
        raw.executescript("BEGIN;" + script + f";PRAGMA user_version={number};COMMIT;")
    raw.execute("ATTACH DATABASE ? AS src", (str(source),))
    for table in ("meta", "counters", "scoring_rules"):
        raw.execute(f"INSERT INTO main.{table} SELECT * FROM src.{table}")
    raw.execute("DETACH DATABASE src")
    assert not raw.execute("SELECT 1 FROM sqlite_master WHERE name='web_tasks'").fetchone()
    raw.close()
    db = store.open_existing(old)
    try:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 6
        inventory = store.inventory(db)
        assert inventory["web"]["tasks"] == 0 and inventory["web"]["knowledge"] == 0
        assert state.get_config(db).enabled is False
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        db.close()


def test_config_defaults_validation_and_persistence(tmp_path):
    path = tmp_path / "i.sqlite"
    db = store.create(path, T0)
    try:
        cfg = state.get_config(db)
        assert cfg.enabled is False and cfg.autonomous is False and cfg.pocket_research is False
        with pytest.raises(Conflict, match=r"nsf web config --enable"):
            state.require_enabled(db)
        version = store.state_version(db)
        out = state.set_config(db, {"enabled": True, "budget_ceiling": {"max_pages": 5},
                                    "freshness_ttl": {"release": 3600}, "deny_domains": ["Tracker.Example"]}, T0)
        assert isinstance(out, wconfig.WebConfig) and out.enabled is True
        assert out.budget_ceiling["max_pages"] == 5 and out.budget_ceiling["max_queries"] == 16
        assert out.freshness_ttl == {"release": 3600.0} and out.deny_domains == ["tracker.example"]
        assert store.state_version(db) == version + 1
        assert state.require_enabled(db).enabled
        for bad in ({"nonsense": 1}, {"enabled": "yes"}, {"min_domain_interval": -1},
                    {"openwebsearch_url": "http://search.example:3210"}, {"openwebsearch_url": "https://127.0.0.1"},
                    {"budget_ceiling": {"max_pagez": 1}}, {"budget_ceiling": {"max_pages": -1}},
                    {"freshness_ttl": {"eternal": 5}}, {"daily_autonomous": {"tasks": 1.5}},
                    {"auto_provisional_min_confidence": 2}, {"auto_provisional_min_sources": 0},
                    {"private_terms": ["ok", ""]}, {"engines": "duckduckgo"}, {}):
            with pytest.raises(ValidationError):
                state.set_config(db, bad, T0)
        assert store.state_version(db) == version + 1
        assert cfg.budget_for("quick_lookup").max_pages == 3
        assert state.get_config(db).budget_for("deep_research").max_pages == 5   # clamped by the ceiling
    finally:
        db.close()
    again = store.open_existing(path)
    try:
        assert state.get_config(again).enabled is True
    finally:
        again.close()


def test_family_digest_is_salted_and_stable(db, tmp_path):
    digest = state.family_digest(db, "family-fingerprint")
    assert re.fullmatch(r"[0-9a-f]{16}", digest) and digest == state.family_digest(db, "family-fingerprint")
    other = store.create(tmp_path / "other.sqlite", T0)
    try:
        assert state.family_digest(other, "family-fingerprint") != digest
    finally:
        other.close()
    with pytest.raises(ValidationError):
        state.family_digest(db, "")


def test_events_clusters_and_strategies_round_trip(db):
    a, b = claim("http://a.example/x"), claim("http://b.example/x")
    tid = task_with(db, [cluster([a, b])])
    with store.write(db):
        state.log_event(db, tid, "decision", {"why": "test"}, T0, stage="discovering")
        with pytest.raises(ValidationError):
            state.log_event(db, tid, "dream", {}, T0)
    kinds = [e["kind"] for e in state.events(db, tid)]
    assert kinds == ["submitted", "decision"]
    loaded = state.load_clusters(db, tid)
    assert len(loaded) == 1 and [c.id for c in loaded[0].claims] == [a.id, b.id]
    assert loaded[0].claims[0].values == a.values and loaded[0].independent_sources == 2
    assert state.claims(db, tid)[0]["cluster_status"] == "corroborated"
    state.save_strategies(db, {"version": 1, "health": {"corpus": {"failures": 2}}}, T0)
    assert state.load_strategies(db)["health"]["corpus"]["failures"] == 2
    with store.write(db):
        state.record_mode_outcome(db, "quick_lookup", "completed", {"requests": 3}, T0)
        state.record_mode_outcome(db, "quick_lookup", "partial", {"requests": 2}, T0)
    stats = state.load_strategies(db, "mode:quick_lookup")
    assert stats == {"runs": 2, "completed": 1, "partial": 1, "total_requests": 5, "total_pages": 0,
                     "total_bytes": 0}
