"""Wave 2, lane U (directive §12; lane brief item 2): a new individual at the shipped defaults behaves as Wave 0 on the
paths that matter, and files written by older software (schema 14, 16, 17, 18) are opened and used.

What already shows the rest, and is cited in docs/genesis/REGRESSION_VERIFICATION.md rather than repeated here: the
offline research benchmark's digest (`scripts/bench_wave1.py offline`, 68731d72… at defaults and with every Wave 1
setting off), `nsf acceptance --all`, the sentence guard's differential (`scripts/guard_differential.py compare`),
the numerical reproduction (`scripts/reproduce.py`), and schema 1-5 and 16 files (tests/instance/test_migrations.py,
tests/instance/test_imagined.py)."""
from contextlib import closing
import json
import sqlite3

import pytest

from neuroscifi import self_description
from neuroscifi.acceptance.cognition import adversarial
from neuroscifi.instance import beliefs, evidence, executive, governance, schema, store
from neuroscifi.webcortex import state

T0 = 1_800_000_000.0
DAY = 86_400.0

# Every Wave 1 setting off: what Wave 0 did (scripts/bench_wave1.py WAVE1_OFF_*; voice settings are not read here)
OFF_INSTANCE = {"internal_state_history": False, "predictive_regulation": False, "executive_rule": "rules-v1",
                "decision_information": False, "imagine_what_if": False, "imagine_forecast": False,
                "imagine_retrospect": False, "imagine_scenarios": False, "broadcast": "off"}
OFF_WEB = {"curiosity_learning_progress": False, "salience_record": False, "hypothesis_fields": False,
           "hypothesis_abandon": False, "premise_recheck": False, "idle_recheck": False, "reflection": False,
           "reflection_model_summary": False, "strategy_selection": "off", "answer_monitoring": False,
           "selective_answering": False, "verification_escalation": False, "adaptive_depth": False,
           "pocket_recent": False}


def _scenario(db):
    """Two epochs of readings (a camera, a settled partner, an ambiguous one, references) and one forgotten partner."""
    for epoch in ("desk", "lab"):
        evidence.start_epoch(db, epoch, T0)
        for i in range(6):
            for channel, root, corrected in (("camera", f"cam-{epoch}", 0), ("word", "ana", 1),
                                             ("reference", f"ref-{i % 3}", 0)):
                n = len(epoch) * 100 + i * 3 + ("camera", "word", "reference").index(channel)
                evidence.observe(db, {"id": f"{epoch}-{n}", "epoch": epoch, "channel": channel, "action": i % 2,
                                      "bit": corrected ^ (i % 2), "root": root}, T0 + n)
        for i, bit in enumerate((0, 1, 0, 1)):
            evidence.observe(db, {"id": f"{epoch}-cara-{i}", "epoch": epoch, "channel": "word", "action": 0,
                                  "bit": bit, "root": "cara"}, T0 + 900 + i)
    evidence.observe(db, {"id": "gone", "epoch": "desk", "channel": "word", "action": 0, "bit": 0, "root": "Bob-x"},
                     T0 + 1001)
    governance.forget_partner(db, "Bob-x", T0 + 1002)


def _read(db):
    """What the paths that matter return: beliefs of both epochs and the executive's decisions (without the display
    keys a Wave 1 setting adds beside the decision: information, processing)."""
    out = {}
    for epoch in ("desk", "lab"):
        read = beliefs.compute(db, epoch)
        read.pop("state_version", None)
        decision = executive.decide(db, epoch, T0 + 2000)
        out[epoch] = {"beliefs": read, "decision": {k: v for k, v in decision.items()
                                                     if k not in ("information", "processing")}}
    return json.dumps(out, sort_keys=True, default=str)


def test_a_new_individual_at_the_shipped_defaults_reads_and_decides_as_with_every_wave1_setting_off(tmp_path):
    reads = {}
    for regime in ("defaults", "off"):
        with closing(store.create(tmp_path / f"{regime}.sqlite", T0)) as db:
            if regime == "off":
                store.set_config(db, OFF_INSTANCE, T0)
                state.set_config(db, OFF_WEB, T0)
            _scenario(db)
            reads[regime] = _read(db)
    assert reads["defaults"] == reads["off"]


def _source(tmp_path):
    """A current individual: the readings scenario and a verified web knowledge item (the real consolidation)."""
    path = tmp_path / "source.sqlite"
    with closing(store.create(path, T0)) as db:
        state.set_config(db, {"enabled": True, "min_domain_interval": 0.0}, T0)
        _scenario(db)
        adversarial._k_item(db, T0 + 10)
    return path


def _old_file(tmp_path, source, version):
    """A file at `version`, made by MIGRATIONS[:version], with the source's rows in the columns that version had."""
    path = tmp_path / f"v{version}.sqlite"
    raw = sqlite3.connect(path, isolation_level=None)
    for number, script in enumerate(schema.MIGRATIONS[:version], start=1):
        raw.executescript("BEGIN;" + script + f";PRAGMA user_version={number};COMMIT;")
    tables = [n for (n,) in raw.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE "
                                        "'sqlite_%' ORDER BY name")]
    raw.execute("ATTACH DATABASE ? AS src", (str(source),))
    raw.execute("BEGIN")
    for table in tables:
        old = [r[1] for r in raw.execute(f"PRAGMA main.table_info({table})")]
        new = {r[1] for r in raw.execute(f"PRAGMA src.table_info({table})")}
        columns = ",".join(c for c in old if c in new)
        raw.execute(f"INSERT INTO main.{table}({columns}) SELECT {columns} FROM src.{table}")
    raw.execute("COMMIT")
    raw.execute("DETACH DATABASE src")
    raw.close()
    return path


def _catalogue(db):
    return sorted(db.execute("SELECT type, name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'").fetchall())


@pytest.mark.parametrize("version", [14, 16, 17, 18])
def test_an_older_file_is_opened_upgraded_and_used(tmp_path, version):
    source = _source(tmp_path)
    with closing(store.connect(source)) as db:
        expected = {e: {k: v for k, v in beliefs.compute(db, e).items() if k != "state_version"}
                    for e in ("desk", "lab")}
        knowledge = db.execute("SELECT id, key, statement, status, confidence FROM web_knowledge").fetchall()
    path = _old_file(tmp_path, source, version)
    with closing(sqlite3.connect(path)) as raw:
        assert raw.execute("PRAGMA user_version").fetchone()[0] == version
    with closing(store.create(tmp_path / "fresh.sqlite", T0)) as fresh:
        catalogue = _catalogue(fresh)
    db = store.open_existing(path)
    try:
        assert db.execute("PRAGMA user_version").fetchone()[0] == schema.SCHEMA_VERSION
        assert _catalogue(db) == catalogue                    # the same tables, indexes and triggers as a new file
        assert db.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        for e in ("desk", "lab"):                             # beliefs, computed on read, bit-identical
            read = beliefs.compute(db, e)
            assert {k: v for k, v in read.items() if k != "state_version"} == expected[e]
        assert db.execute("SELECT id, key, statement, status, confidence FROM web_knowledge").fetchall() == knowledge
        # ... and used: a decision, a new reading, a new consolidation, a forget, the self-description
        assert executive.decide(db, "desk", T0 + 3000, record=True)["decision"]
        evidence.observe(db, {"id": "after", "epoch": "desk", "channel": "camera", "action": 0, "bit": 0,
                              "root": "cam-desk"}, T0 + 3001)
        state.set_config(db, {"enabled": True, "min_domain_interval": 0.0}, T0 + 3002)
        events = db.execute("SELECT count(*) FROM web_events WHERE kind='consolidated'").fetchone()[0]
        adversarial._k_item(db, T0 + 2 * DAY, hosts=("k3.example", "k4.example"))
        assert db.execute("SELECT count(*) FROM web_events WHERE kind='consolidated'").fetchone()[0] == events + 1
        kid = db.execute("SELECT max(id) FROM web_knowledge").fetchone()[0]
        governance.forget_knowledge(db, kid, T0 + 3 * DAY)
        assert db.execute("SELECT count(*) FROM web_knowledge WHERE id=?", (kid,)).fetchone()[0] == 0
        record = self_description.build(db, path, T0 + 3 * DAY)
        broken = [k for k, v in record.items() if isinstance(v, dict) and v.get("error")]
        assert not broken, broken
    finally:
        db.close()
