"""A web setting change leaves an audit row like an instance setting's (OMNIMIND lane S, audit-cognition hazard 9).

state.set_config writes one deletion_log row per setting it changed (target_kind 'web_setting', target_id the key;
effects: who and the state version, never a value, which may be private: private_terms, provider caps), in the
transaction that changes the setting. Nothing that reads the log changes its behaviour: a checkpoint written while
a web setting changes is not taken as racing a deletion, the quiet switch's last change is still the instance
setting's, and the console's memory log lists the row."""
import json
import sqlite3

import pytest

from neuroscifi import archive, background
from neuroscifi.instance import checkpoints, memory, store
from neuroscifi.instance.errors import ValidationError
from neuroscifi.webcortex import state

T0 = 1_800_000_000.0
SECRET = "Zelda-private-77"


@pytest.fixture
def individual(tmp_path):
    path = tmp_path / "individual.sqlite"
    db = store.create(path, T0)
    yield db, path
    db.close()


def rows(db):
    return db.execute("SELECT target_kind, target_id, requested_at, effects FROM deletion_log ORDER BY id").fetchall()


def test_each_changed_web_setting_is_logged_with_who_and_when_and_no_value(individual):
    db, _ = individual
    state.set_config(db, {"enabled": True, "private_terms": [SECRET], "daily_provider_queries": {"brave": 3}},
                     T0 + 1, by="owner:console")
    version = store.state_version(db)
    assert [(k, t, at) for k, t, at, _ in rows(db)] == [
        ("web_setting", "daily_provider_queries", T0 + 1), ("web_setting", "enabled", T0 + 1),
        ("web_setting", "private_terms", T0 + 1)]
    assert {json.dumps(json.loads(e), sort_keys=True) for *_, e in rows(db)} == {
        json.dumps({"by": "owner:console", "state_version": version}, sort_keys=True)}
    assert SECRET not in json.dumps(rows(db)) and "brave" not in json.dumps(rows(db))


def test_an_unchanged_or_refused_setting_leaves_no_row(individual):
    db, _ = individual
    state.set_config(db, {"enabled": False}, T0 + 1)                     # already off: nothing changed
    with pytest.raises(ValidationError):
        state.set_config(db, {"enabled": "yes"}, T0 + 2)
    with pytest.raises(ValidationError):
        state.set_config(db, {"no_such_key": 1}, T0 + 3)
    assert rows(db) == []
    state.set_config(db, {"enabled": True}, T0 + 4)
    assert [(t, json.loads(e)["by"]) for _, t, _, e in rows(db)] == [("enabled", "owner")]


def test_the_row_is_written_in_the_transaction_that_changes_the_setting(individual):
    db, _ = individual
    db.execute("CREATE TRIGGER no_log BEFORE INSERT ON deletion_log BEGIN SELECT RAISE(ABORT, 'log refused'); END")
    with pytest.raises(sqlite3.IntegrityError):
        state.set_config(db, {"enabled": True}, T0 + 1)
    assert state.get_config(db).enabled is False                          # no change without its row


def test_readers_of_the_log_are_unchanged(individual, tmp_path, monkeypatch):
    db, path = individual
    real = archive.convert

    def convert_while_the_owner_changes_a_web_setting(*args, **kwargs):
        writer = store.open_existing(path)
        try:
            state.set_config(writer, {"enabled": True}, T0 + 2)
        finally:
            writer.close()
        return real(*args, **kwargs)
    monkeypatch.setattr(archive, "convert", convert_while_the_owner_changes_a_web_setting)
    cp = checkpoints.create(db, path, tmp_path / "cps", T0 + 3)
    assert cp["status"] == "valid" and "warning" not in cp             # a setting is not a deletion
    assert background.status(db)["changed_at"] is None                  # quiet's own row, not a web setting's
    listed = memory.retrieve(db, "deletion", limit=10)["items"]
    assert [(i["payload"]["target_kind"], i["payload"]["target_id"]) for i in listed] == [("web_setting", "enabled")]
