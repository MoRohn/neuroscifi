"""Importing a prototype legacy_live database: same beliefs, read-only, idempotent, forgetting respected."""
import hashlib
import json
import random
import sqlite3

import pytest

from neuroscifi import legacy_live
from neuroscifi.cli import main
from neuroscifi.instance import beliefs, evidence, legacy, store
from neuroscifi.instance.errors import Conflict, NotFound, ValidationError

from conftest import T0


def build_prototype(path, seed=7):
    """A prototype DB with two epochs of mixed camera/word/reference events; returns its status per epoch."""
    rng = random.Random(seed)
    db = legacy_live.open_store(path)
    with db:
        for name in ("desk", "garage"):
            db.execute("INSERT INTO epochs VALUES (?)", (name,))
    for epoch in ("desk", "garage"):
        for i in range(30):
            channel = rng.choice(["camera", "word", "reference"])
            root = {"camera": "cam-a", "word": "ana"}.get(channel) or f"ref-{rng.randrange(3)}"
            legacy_live.add_event(db, epoch, f"{epoch}-{rng.getrandbits(32):08x}", channel,
                                  rng.randrange(2), int(rng.random() < .8), root)
    status = {epoch: legacy_live.status(db, epoch) for epoch in ("desk", "garage")}
    db.close()
    return status


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_import_reproduces_prototype_beliefs(instance, tmp_path):
    db, _ = instance
    proto = tmp_path / "live.sqlite"
    expected = build_prototype(proto)
    before = digest(proto)
    out = legacy.import_legacy(db, proto, T0)
    assert out == {"epochs": 2, "evidence": 60, "already_present": 0, "forgotten_skipped": 0}
    assert digest(proto) == before  # opened read-only
    for epoch, status in expected.items():
        result = beliefs.compute(db, epoch)
        assert json.dumps(result["legacy"], sort_keys=True) == json.dumps(status, sort_keys=True)
        assert result["mechanism"]["p_inverted"] == pytest.approx(
            status["probability_inverted"]["mechanism"], abs=1e-12)
    assert db.execute("SELECT DISTINCT retention, expires_at, provenance, consent, recorded_at FROM evidence"
                      ).fetchall() == [("promoted", None, "observed", "learn", T0)]
    assert store.counter(db, "evidence_written") == 60
    causes = [r[0] for r in db.execute("SELECT cause FROM belief_revisions ORDER BY id")]
    assert causes == ["legacy_import", "legacy_import"]


def test_reimport_is_idempotent_and_forgotten_ids_stay_forgotten(instance, tmp_path):
    db, _ = instance
    proto = tmp_path / "live.sqlite"
    build_prototype(proto)
    legacy.import_legacy(db, proto, T0)
    version = store.state_version(db)
    again = legacy.import_legacy(db, proto, T0 + 60)
    assert again == {"epochs": 0, "evidence": 0, "already_present": 60, "forgotten_skipped": 0}
    assert store.state_version(db) == version
    gone = db.execute("SELECT id, seq, epoch FROM evidence ORDER BY seq LIMIT 1").fetchone()
    with store.write(db):  # simulate governance forgetting one imported event
        db.execute("DELETE FROM evidence WHERE id=?", (gone[0],))
        db.execute("INSERT INTO tombstones VALUES(?,?,?,?, 'forgotten')", (*gone, T0))
    third = legacy.import_legacy(db, proto, T0 + 120)
    assert third == {"epochs": 0, "evidence": 0, "already_present": 59, "forgotten_skipped": 1}
    assert db.execute("SELECT count(*) FROM evidence WHERE id=?", (gone[0],)).fetchone()[0] == 0


def test_conflicting_id_aborts_the_whole_import(instance, tmp_path):
    db, _ = instance
    proto = tmp_path / "live.sqlite"
    build_prototype(proto)
    source = sqlite3.connect(proto)
    first = source.execute("SELECT id, epoch, channel, action, bit, source_root FROM observations "
                           "ORDER BY id").fetchone()
    source.close()
    evidence.start_epoch(db, first[1], T0)
    evidence.observe(db, {"id": first[0], "epoch": first[1], "channel": first[2], "action": first[3],
                          "bit": 1 - first[4], "root": first[5]}, T0)
    with pytest.raises(Conflict):
        legacy.import_legacy(db, proto, T0)
    assert db.execute("SELECT count(*) FROM evidence").fetchone()[0] == 1


def test_missing_or_foreign_files_are_rejected(instance, tmp_path):
    db, path = instance
    with pytest.raises(NotFound):
        legacy.import_legacy(db, tmp_path / "absent.sqlite", T0)
    with pytest.raises(ValidationError):
        legacy.import_legacy(db, path, T0)  # an individual, not a prototype database
    junk = tmp_path / "junk.sqlite"
    junk.write_bytes(b"not a database at all" * 100)
    with pytest.raises(ValidationError):
        legacy.import_legacy(db, junk, T0)
    bad = tmp_path / "bad.sqlite"
    proto = legacy_live.open_store(bad)
    with proto:
        proto.execute("INSERT INTO epochs VALUES ('desk')")
        proto.execute("INSERT INTO observations VALUES ('e1','desk','smell',0,1,'nose')")
    proto.close()
    with pytest.raises(ValidationError, match="e1"):
        legacy.import_legacy(db, bad, T0)


def test_cli_import_legacy(tmp_path, capsys):
    proto = tmp_path / "live.sqlite"
    expected = build_prototype(proto)
    db = tmp_path / "cli.sqlite"
    assert main(["init", "--db", str(db)]) == 0
    capsys.readouterr()
    assert main(["import-legacy", str(proto), "--db", str(db)]) == 0
    assert json.loads(capsys.readouterr().out)["evidence"] == 60
    assert main(["status", "garage", "--db", str(db)]) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["legacy"]["state_probabilities_in_binary_order"] == pytest.approx(
        expected["garage"]["state_probabilities_in_binary_order"], abs=1e-12)
    instance = store.open_existing(db)
    try:
        assert instance.execute("SELECT count(*) FROM evidence").fetchone()[0] == 60
    finally:
        instance.close()
