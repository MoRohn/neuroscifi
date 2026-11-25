"""Schema 16 (the copied flags): which consolidated readings may be a later copy of a sample, and a restore that streams tombstones.

Beliefs count the earliest copy of a sample (ADR-044). Finding the samples with more than one reading used to be a
grouped pass over every reading of the epoch on every belief computation, even when no sample was recorded twice.
`evidence_consolidated.copied`, kept by triggers, lets the samples with several readings be found from the flagged
readings. The rule the triggers keep: of any two readings of one sample, at least one is flagged. These tests hold
it under random inserts, deletes and updates, through the migration of a version-14 file, and check that beliefs are
the same numbers on every path. A restore no longer reads every tombstone into memory: it applies them in batches
inside one snapshot of the live file.
"""
from contextlib import closing
import random
import sqlite3

import pytest

from neuroscifi.instance import beliefs, checkpoints, evidence, governance, schema, store

from conftest import T0
import test_consolidation as tc

DAY = 86400.0
# The schema version that adds evidence_consolidated.copied: its place in the list of migrations.
FLAGS = 1 + next(i for i, script in enumerate(schema.MIGRATIONS) if "ADD COLUMN copied" in script)


def _pairs_unflagged(db):
    """Samples with two or more unflagged readings: the rule says there are none."""
    return db.execute(
        "SELECT epoch, channel, root, sample_id, count(*) FROM evidence_consolidated WHERE sample_id IS NOT NULL "
        "AND copied=0 GROUP BY epoch, channel, root, sample_id HAVING count(*)>1").fetchall()


@pytest.mark.parametrize("seed", range(6))
def test_of_any_two_readings_of_a_sample_one_is_flagged_under_random_writes(instance, seed):
    db, _ = instance
    rng = random.Random(seed)
    for name in ("e", "f"):
        evidence.start_epoch(db, name, T0)
    seq = 0
    for step in range(600):
        r = rng.random()
        with store.write(db):
            if r < .55:
                seq += rng.randrange(1, 4)
                if rng.random() < .9:
                    verb, target = rng.choice(["INSERT", "INSERT OR IGNORE", "INSERT OR REPLACE"]), seq
                else:           # sometimes onto a seq that may exist
                    verb, target = rng.choice(["INSERT OR IGNORE", "INSERT OR REPLACE"]), rng.randrange(1, seq + 1)
                db.execute(f"{verb} INTO evidence_consolidated(seq,epoch,channel,root,value,sample_id,"
                           "consolidated_at) VALUES(?,?,?,?,?,?,?)",
                           (target, rng.choice("ef"), rng.choice(["camera", "word"]), f"r{rng.randrange(3)}",
                            rng.randrange(2), rng.choice([None, *(f"s{k}" for k in range(5))]), T0))
            elif r < .8:
                db.execute("DELETE FROM evidence_consolidated WHERE seq=(SELECT seq FROM evidence_consolidated "
                           "ORDER BY random() LIMIT 1)")
            else:
                column, value = rng.choice([("root", f"r{rng.randrange(3)}"), ("sample_id", f"s{rng.randrange(5)}"),
                                            ("sample_id", None), ("epoch", rng.choice("ef")),
                                            ("channel", rng.choice(["camera", "word"]))])
                db.execute(f"UPDATE evidence_consolidated SET {column}=? WHERE seq=(SELECT seq FROM "
                           "evidence_consolidated ORDER BY random() LIMIT 1)", (value,))
        assert _pairs_unflagged(db) == [], f"step {step}"
        if step % 50 == 0:
            for epoch in "ef":
                exact = db.execute(
                    "SELECT channel, root, sample_id, min(seq), count(*) FROM evidence_consolidated WHERE epoch=? "
                    "AND sample_id IS NOT NULL GROUP BY channel, root, sample_id HAVING count(*)>1 "
                    "ORDER BY 1, 2, 3", (epoch,)).fetchall()
                assert sorted(beliefs._copied_samples(db, epoch)) == exact


def test_a_flag_is_never_cleared(instance):
    db, _ = instance
    evidence.start_epoch(db, "e", T0)
    with store.write(db):
        db.executemany("INSERT INTO evidence_consolidated(seq,epoch,channel,root,value,sample_id,consolidated_at) "
                       "VALUES(?,'e','camera','c',1,'s',?)", [(1, T0), (2, T0)])
    assert db.execute("SELECT seq, copied FROM evidence_consolidated ORDER BY seq").fetchall() == [(1, 0), (2, 1)]
    with pytest.raises(sqlite3.IntegrityError, match="never cleared"):
        with store.write(db):
            db.execute("UPDATE evidence_consolidated SET copied=0")
    with store.write(db):
        db.execute("DELETE FROM evidence_consolidated WHERE seq=1")
    # stale: seq 2 is now the only reading of its sample; beliefs look at it and find one reading
    assert db.execute("SELECT copied FROM evidence_consolidated").fetchall() == [(1,)]
    assert beliefs._copied_samples(db, "e") == []


def _statements(db, fn):
    seen = []
    db.set_trace_callback(seen.append)
    try:
        fn()
    finally:
        db.set_trace_callback(None)
    return seen


def test_no_pass_over_the_readings_when_no_sample_was_recorded_twice(instance):
    """The cost this schema removes: with every reading carrying its own sample id, a belief computation no longer
    groups the epoch's readings by sample; with one sample recorded twice it reads that sample by key."""
    db, _ = instance
    evidence.start_epoch(db, "e", T0)
    rows = [(k, "e", "camera" if k % 2 else "word", f"r{k % 7}", k % 3 == 0, f"s{k}", T0) for k in range(1, 400)]
    with store.write(db):
        db.executemany("INSERT INTO evidence_consolidated(seq,epoch,channel,root,value,sample_id,consolidated_at) "
                       "VALUES(?,?,?,?,?,?,?)", rows)
        store.bump_state_version(db)
    grouped = [s for s in _statements(db, lambda: beliefs.compute(db, "e")) if "HAVING count(*)>1" in s]
    assert grouped == []
    with store.write(db):
        db.execute("INSERT INTO evidence_consolidated(seq,epoch,channel,root,value,sample_id,consolidated_at) "
                   "VALUES(500,'e','camera','r1',0,'s1',?)", (T0,))
        store.bump_state_version(db)
    assert beliefs._copied_samples(db, "e") == [("camera", "r1", "s1", 1, 2)]
    grouped = [s for s in _statements(db, lambda: beliefs.compute(db, "e")) if "HAVING count(*)>1" in s]
    assert grouped == []
    assert beliefs.compute(db, "e")["evidence"]["duplicates_ignored"] == 1


@pytest.mark.parametrize("seed", range(6))
def test_beliefs_are_the_same_numbers_on_every_path(instance, seed, monkeypatch):
    """The randomized differential: a life with copies, promotions, forgets and sweeps, everything believed computed
    from the flags, by the grouped pass (as before the flags) and with the flags ignored; exactly equal."""
    db, _ = instance
    tc._random_life(db, seed)
    rng = random.Random(seed)
    for days in (7.2, 8.5, 11, 14, 400):
        governance.sweep(db, T0 + days * DAY)
        if rng.random() < .7:
            seq = db.execute("SELECT seq FROM evidence_consolidated WHERE sample_id IS NOT NULL ORDER BY random() "
                             "LIMIT 1").fetchone()
            if seq:
                governance.forget_consolidated(db, seq[0], T0 + days * DAY)
        flagged = tc._everything_believed(db)
        with monkeypatch.context() as m:
            m.setattr(beliefs, "_FEW_COPIED_SAMPLES", -1)
            grouped = tc._everything_believed(db)
        with monkeypatch.context() as m:
            m.setattr(beliefs, "_has_copied_index", lambda db: False)
            unflagged = tc._everything_believed(db)
        assert flagged == grouped == unflagged, f"day {days}"
        assert _pairs_unflagged(db) == []


def _before_the_flags(path):
    """Take the copied flags off a file: what the schema before them leaves. (Their migration is the last one.)"""
    with closing(sqlite3.connect(path, isolation_level=None)) as raw:
        raw.executescript(
            "BEGIN; DROP TRIGGER evidence_consolidated_copied_insert; DROP TRIGGER evidence_consolidated_copied_update; "
            "DROP TRIGGER evidence_consolidated_copied_kept; DROP INDEX evidence_consolidated_copied; "
            "ALTER TABLE evidence_consolidated DROP COLUMN copied; "
            f"PRAGMA user_version={FLAGS - 1}; COMMIT;")


def test_a_file_from_before_the_flags_gains_them_and_believes_the_same(tmp_path):
    path = tmp_path / "before.sqlite"
    db = store.create(path, T0)
    try:
        tc._random_life(db, 3)
        governance.sweep(db, T0 + 400 * DAY)
        before = tc._everything_believed(db)
        rows = tc.folded(db)
        later = db.execute("SELECT count(*) FROM evidence_consolidated WHERE copied=1").fetchone()[0]
        assert later > 0
    finally:
        db.close()
    _before_the_flags(path)
    db = store.open_existing(path)
    try:
        assert db.execute("PRAGMA user_version").fetchone()[0] == schema.SCHEMA_VERSION >= FLAGS
        assert db.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert tc.folded(db) == rows and _pairs_unflagged(db) == []
        # the migration flags exactly the readings with an earlier reading of their sample
        assert db.execute("SELECT count(*) FROM evidence_consolidated WHERE copied=1").fetchone()[0] == later
        assert tc._everything_believed(db) == before
        # and the triggers are in place for what comes next
        epoch, channel, root, sample = db.execute(
            "SELECT epoch, channel, root, sample_id FROM evidence_consolidated WHERE sample_id IS NOT NULL "
            "AND copied=0 LIMIT 1").fetchone()
        with store.write(db):
            db.execute("INSERT INTO evidence_consolidated(seq,epoch,channel,root,value,sample_id,consolidated_at) "
                       "VALUES(100000,?,?,?,0,?,?)", (epoch, channel, root, sample, T0))
        assert db.execute("SELECT copied FROM evidence_consolidated WHERE seq=100000").fetchone() == (1,)
    finally:
        db.close()


# ---- a restore streams the tombstones -------------------------------------------------------------------------------

def _dump(path):
    with closing(sqlite3.connect(path)) as raw:
        tables = [name for (name,) in raw.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        return {t: raw.execute(f"SELECT * FROM {t} ORDER BY rowid").fetchall() for t in tables}


def _life_with_an_archive(db, path, tmp_path):
    tc._random_life(db, 5)
    archive = checkpoints.create(db, path, tmp_path / "cps", T0 + 1)
    governance.sweep(db, T0 + 9 * DAY)
    for victim in ("e3", "e17", "e40"):
        try:
            governance.forget_evidence(db, victim, T0 + 9 * DAY)
        except Exception:       # noqa: BLE001 - an event of the random life may have been forgotten or expired
            pass
    governance.sweep(db, T0 + 30 * DAY)
    return archive


def test_a_restore_applies_the_tombstones_in_batches_with_the_same_result(instance, tmp_path, monkeypatch):
    db, path = instance
    archive = _life_with_an_archive(db, path, tmp_path)
    whole = checkpoints.restore(db, archive["id"], tmp_path / "whole.sqlite", T0 + 31 * DAY)
    monkeypatch.setattr(checkpoints, "TOMBSTONE_BATCH", 3)
    batched = checkpoints.restore(db, archive["id"], tmp_path / "batched.sqlite", T0 + 31 * DAY)
    assert whole["tombstones_applied"] == batched["tombstones_applied"] == \
        db.execute("SELECT count(*) FROM tombstones").fetchone()[0] > 100
    assert {k: v for k, v in whole.items() if k != "path"} == {k: v for k, v in batched.items() if k != "path"}
    assert _dump(tmp_path / "whole.sqlite") == _dump(tmp_path / "batched.sqlite")
    assert not db.in_transaction


def test_the_tombstones_a_restore_applies_are_those_of_its_snapshot(instance, tmp_path, monkeypatch):
    """The live file is read in one snapshot (a forget is wholly in it or not at all). The tombstones are read while
    the replay runs, so the snapshot must hold until it ends: a tombstone written meanwhile is not applied."""
    db, path = instance
    archive = _life_with_an_archive(db, path, tmp_path)
    before = db.execute("SELECT count(*) FROM tombstones").fetchone()[0]
    monkeypatch.setattr(checkpoints, "TOMBSTONE_BATCH", 1)
    original = checkpoints._Tombstones.__iter__
    other = sqlite3.connect(path, isolation_level=None)

    def interrupted(self):
        for k, row in enumerate(original(self)):
            if k == 1:      # a writer adds a tombstone while the replay is reading them
                other.execute("INSERT INTO tombstones(id,seq,epoch,deleted_at,reason) "
                              "VALUES('late',999999,'a',?,'forgotten')", (T0,))
            yield row

    monkeypatch.setattr(checkpoints._Tombstones, "__iter__", interrupted)
    try:
        result = checkpoints.restore(db, archive["id"], tmp_path / "copy.sqlite", T0 + 31 * DAY)
    finally:
        other.close()
    assert result["tombstones_applied"] == before
    with closing(sqlite3.connect(tmp_path / "copy.sqlite")) as copy:
        assert copy.execute("SELECT 1 FROM tombstones WHERE id='late'").fetchone() is None
    assert db.execute("SELECT count(*) FROM tombstones").fetchone()[0] == before + 1


def test_more_flagged_samples_than_are_looked_up_go_to_the_grouped_pass(instance, monkeypatch):
    """Stale flags count toward the limit of samples looked up by key: past it, the flagged samples read are not all
    of them, and the grouped pass decides. Here the one sample really recorded twice is the last flagged one."""
    db, _ = instance
    monkeypatch.setattr(beliefs, "_FEW_COPIED_SAMPLES", 2)
    evidence.start_epoch(db, "e", T0)
    with store.write(db):
        seq = 0
        for sample in ("s1", "s2", "s3", "s4"):
            for _ in range(2):
                seq += 1
                db.execute("INSERT INTO evidence_consolidated(seq,epoch,channel,root,value,sample_id,consolidated_at) "
                           "VALUES(?,'e','camera','c',1,?,?)", (seq, sample, T0))
        db.execute("DELETE FROM evidence_consolidated WHERE seq IN (1, 3, 5)")     # s1 to s3: one stale reading each
        store.bump_state_version(db)
    assert db.execute("SELECT count(*) FROM evidence_consolidated WHERE copied=1").fetchone()[0] == 4
    assert beliefs._copied_samples(db, "e") == [("camera", "c", "s4", 7, 2)]
    assert beliefs.compute(db, "e")["evidence"]["duplicates_ignored"] == 1
