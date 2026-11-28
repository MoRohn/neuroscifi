"""No evidence-side table may name an artifact (schema 20, ADR-071; audit-cognition Hazard 3, contracts C3/C4): the
reference columns test_imagined.py checks for imagined ids refuse an artifact id too, in every form an id may take
there, and free text that reads like one is never refused. Also: the migration is appended after version 19 and
changes no row of an older file."""
from contextlib import closing
import json
import sqlite3

import pytest

from neuroscifi.instance import schema
from test_imagined import _UNIQUE, REFERENCE_COLUMNS, _seed_rows

ART = "artifact-" + "0123456789abcdef" * 2
VALUES = {"whole": [ART, ART.upper(), f" {ART}", f"{ART}\n", f"\t{ART} "],
          "inside": [f"evidence:{ART}", f"evidence:{ART.upper()}"],
          "list": [json.dumps([ART]), f'[ "{ART}" ]', json.dumps(["c1", ART.upper()]),
                   '["\\u0061rtifact-' + ART.split("-")[1] + '"]']}
VALUES["objects"] = VALUES["list"] + [json.dumps([{"claim": ART, "text": "q"}]),
                                      json.dumps([{"claim": "c1", "task": f" {ART}"}]),
                                      json.dumps([{"claim_id": ART.upper()}])]
BENIGN = {"whole": "artifact-of-the-past", "inside": "evidence:artifact-not-an-id",
          "list": json.dumps(["artifact-x", "c1"]),
          "objects": json.dumps([{"claim": "c1", "task": "t1", "text": ART, "url": f"http://a/{ART}"}])}
CASES = [(t, c, f, n) for t, cols in REFERENCE_COLUMNS.items() for c, f in cols.items() for n in range(len(VALUES[f]))]


@pytest.mark.parametrize("table,column,form,n", CASES, ids=[f"{t}.{c}-{n}" for t, c, _, n in CASES])
def test_no_evidence_side_column_may_name_an_artifact(instance, table, column, form, n):
    db, _ = instance
    _seed_rows(db)
    value = VALUES[form][n]
    rowid = db.execute(f"SELECT rowid FROM {table} LIMIT 1").fetchone()[0]
    with pytest.raises(sqlite3.IntegrityError, match="artifact is never evidence"):
        db.execute(f"UPDATE {table} SET {column}=? WHERE rowid=?", (value, rowid))
    names = [r[1] for r in db.execute(f"PRAGMA table_info({table})") if r[1] not in ("id", "seq") or r[1] == column]
    picks = ", ".join("?" if c == column else f"{c}||'2'" if c == _UNIQUE.get(table) else c for c in names)
    with pytest.raises(sqlite3.IntegrityError, match="artifact is never evidence"):
        db.execute(f"INSERT INTO {table}({','.join(names)}) SELECT {picks} FROM {table} WHERE rowid=?",
                   (value, rowid))
    db.execute(f"UPDATE {table} SET {column}=? WHERE rowid=?", (BENIGN[form], rowid))     # no false alarm


def test_every_reference_column_has_its_artifact_triggers(instance):
    db, _ = instance
    triggers = {name for (name,) in db.execute("SELECT name FROM sqlite_master WHERE type='trigger'")}
    for table in REFERENCE_COLUMNS:
        assert {f"{table}_no_artifact_i", f"{table}_no_artifact_u"} <= triggers, table
    assert set(REFERENCE_COLUMNS) == set(schema._NO_IMAGINED)


def test_version_20_is_the_artifact_index_appended_last_and_changes_no_older_row(tmp_path):
    number = next(i for i, script in enumerate(schema.MIGRATIONS) if "CREATE TABLE IF NOT EXISTS artifacts(" in script)
    assert number + 1 == 20 <= schema.SCHEMA_VERSION == len(schema.MIGRATIONS)    # 21: lane L (ADR-078)
    assert next(i for i, s in enumerate(schema.MIGRATIONS) if "web_events_kind_time" in s) + 1 == 19
    path = tmp_path / "v19.sqlite"
    with closing(sqlite3.connect(path, isolation_level=None)) as raw:
        for version, script in enumerate(schema.MIGRATIONS[:number], start=1):
            raw.executescript("BEGIN;" + script + f";PRAGMA user_version={version};COMMIT;")
        raw.execute("INSERT INTO epochs(name,created_at) VALUES('e1',1)")
        raw.execute("INSERT INTO evidence(id,epoch,channel,action,bit,root,recorded_at) VALUES('a','e1','word',0,1,'r',1)")
        before = raw.execute("SELECT * FROM evidence").fetchall()
        schema.migrate(raw)
        assert raw.execute("PRAGMA user_version").fetchone()[0] == schema.SCHEMA_VERSION
        assert raw.execute("SELECT * FROM evidence").fetchall() == before
        assert raw.execute("SELECT count(*) FROM artifacts").fetchone()[0] == 0
        raw.executescript(schema.MIGRATIONS[number])          # IF NOT EXISTS throughout: a second run is harmless
