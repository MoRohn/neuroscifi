"""The limit on consolidated readings (ADR-044, InstanceConfig.max_consolidated_rows) and what a restore holds in
memory.

Consolidated readings never expire, so without a limit a file grows for as long as its individual lives. The limit
is a guard rail, not a compaction: at the limit the sweep stops adding readings (an expiring learning event leaves
nothing, as before schema 14) and says so wherever the owner looks; from 80 % it warns. It never removes a reading.
"""
from array import array
from contextlib import closing
import json
import sqlite3

import pytest

from neuroscifi.instance import beliefs, checkpoints, executive, governance, memory, store
from neuroscifi.instance.errors import ValidationError

from conftest import T0
from test_consolidation import DAY, count, folded, held, obs, teach


def limited(db, limit, now=T0 + 1):
    return store.set_config(db, {"max_consolidated_rows": limit}, now)


# ---- the setting ---------------------------------------------------------------------------------------------------

def test_the_default_limit_is_a_million_and_an_older_settings_row_gets_it(instance):
    db, _ = instance
    assert store.config(db).max_consolidated_rows == 1_000_000 == store.InstanceConfig.max_consolidated_rows
    with store.write(db):                # settings saved by the first schema-14 build: no such field
        raw = json.loads(db.execute("SELECT value FROM meta WHERE key='config'").fetchone()[0])
        del raw["max_consolidated_rows"]
        db.execute("UPDATE meta SET value=? WHERE key='config'", (json.dumps(raw),))
    assert store.config(db).max_consolidated_rows == 1_000_000 and store.setting_problems(db) == []
    assert store.regulation(db)["config"]["max_consolidated_rows"] == 1_000_000


@pytest.mark.parametrize("bad", [-1, 1.5, "100", True, None, 10**12 + 1])
def test_the_limit_must_be_a_whole_number_from_zero_up(instance, bad):
    db, _ = instance
    version = store.state_version(db)
    with pytest.raises(ValidationError):
        limited(db, bad)
    assert store.state_version(db) == version and store.config(db).max_consolidated_rows == 1_000_000


def test_changing_the_limit_is_logged_like_the_other_settings(instance):
    db, _ = instance
    assert limited(db, 0, T0 + 5) == {"config": store.regulation(db)["config"], "changed": ["max_consolidated_rows"]}
    assert store.config(db).max_consolidated_rows == 0
    assert limited(db, 0, T0 + 6)["changed"] == []                       # the same value changes nothing
    items = memory.retrieve(db, "deletion")["items"]
    assert [(i["payload"]["target_kind"], i["payload"]["target_id"]) for i in items] == [
        ("setting", "max_consolidated_rows")]
    assert items[0]["payload"]["effects"]["from"] == 1_000_000 and items[0]["payload"]["effects"]["to"] == 0


@pytest.mark.parametrize("damaged", ['"none"', "-5", "true", "2.5", "null"])
def test_a_damaged_limit_is_never_read_as_no_limit(instance, damaged):
    """A hand-edited value that is not a limit must not switch the limit off by accident: the default stands, and the
    owner is told."""
    db, _ = instance
    with store.write(db):
        raw = json.loads(db.execute("SELECT value FROM meta WHERE key='config'").fetchone()[0])
        text = json.dumps(raw).replace('"max_consolidated_rows": 1000000', f'"max_consolidated_rows": {damaged}')
        assert text != json.dumps(raw)
        db.execute("UPDATE meta SET value=? WHERE key='config'", (text,))
    assert store.config(db).max_consolidated_rows == 1_000_000
    problems = store.setting_problems(db)
    assert len(problems) == 1 and "max_consolidated_rows" in problems[0] and "default limit of 1000000" in problems[0]
    assert limited(db, 7)["changed"] == ["max_consolidated_rows"]        # setting it repairs the row
    assert store.setting_problems(db) == [] and store.config(db).max_consolidated_rows == 7


# ---- the sweep at, below and across the limit ----------------------------------------------------------------------

def test_below_the_limit_a_sweep_is_what_it_was(instance):
    db, _ = instance
    teach(db)                                                # 18 learning events
    limited(db, 100)
    before = held(db, "desk")
    out = governance.sweep(db, T0 + 8 * DAY)
    assert out["expired"] == 18 and out["consolidated"] == 18
    assert "not_consolidated" not in out and "warnings" not in out
    assert held(db, "desk") == before
    regulation = store.regulation(db)
    assert regulation["consolidated_readings"] == 18 and "warnings" not in regulation
    assert "warnings" not in store.inventory(db)


def test_a_sweep_that_would_cross_the_limit_keeps_the_oldest_events_and_says_how_many_of_each(instance):
    db, _ = instance
    ids = teach(db)                                          # seqs 1..18 in the order of ids
    obs(db, "quiet", "desk", "word", "ana", 1, T0 + 30, consent="memory_only")       # seq 19: never a reading
    limited(db, 10)
    out = governance.sweep(db, T0 + 8 * DAY)
    assert out["expired"] == 19 and out["consolidated"] == 10 and out["not_consolidated"] == 8
    assert [row[0] for row in folded(db)] == list(range(1, 11))          # the oldest by their place in the journal
    assert count(db, "evidence") == 0 and count(db, "tombstones") == 19  # every event expired and is Gone, as ever
    assert len(out["warnings"]) == 1
    warning = out["warnings"][0]
    assert warning.startswith("The limit on consolidated readings is reached (10 of 10)")
    assert "leaves nothing" in warning and "Nothing already kept is removed" in warning
    assert "nsf config --max-consolidated-rows" in warning and "nsf forget epoch|partner|camera NAME" in warning
    # what was kept is what counts now: exactly the beliefs of an individual that only ever saw those ten events
    assert beliefs.compute(db, "desk")["evidence"]["learning"] == 10
    revision = json.loads(db.execute("SELECT summary FROM belief_revisions WHERE cause='expired' ORDER BY id DESC "
                                     "LIMIT 1").fetchone()[0])
    assert revision["consolidated_now"] == 10 and revision["consolidation_limit_reached"] is True
    logged = json.loads(db.execute("SELECT effects FROM deletion_log WHERE target_kind='sweep'").fetchone()[0])
    assert logged["consolidated"] == 10 and logged["not_consolidated"] == 8
    assert ids[0] not in json.dumps(logged)


def test_at_the_limit_an_expiring_event_leaves_nothing_and_every_place_says_so(instance):
    db, _ = instance
    teach(db, rounds=2)                                      # 6 events
    limited(db, 6)
    assert governance.sweep(db, T0 + 8 * DAY)["consolidated"] == 6
    kept = folded(db)
    before = held(db, "desk")
    for k in range(3):
        obs(db, f"late-{k}", "desk", "camera", "cam-a", 0, T0 + 9 * DAY + k)        # readings against the belief
    out = governance.sweep(db, T0 + 17 * DAY)
    assert out["expired"] == 3 and "consolidated" not in out and out["not_consolidated"] == 3
    assert folded(db) == kept and held(db, "desk") == before             # nothing kept was touched, nothing added
    full = "The limit on consolidated readings is reached (6 of 6)"
    assert out["warnings"][0].startswith(full)
    assert store.regulation(db)["warnings"][0].startswith(full)
    assert store.inventory(db)["warnings"][0].startswith(full)
    # the next sweep, with nothing to expire, costs no count and says nothing; the standing places still do
    assert "warnings" not in governance.sweep(db, T0 + 17 * DAY + 1)
    assert store.regulation(db)["warnings"][0].startswith(full)
    # raising the limit lets the next expiry in again
    limited(db, 8, T0 + 18 * DAY)
    obs(db, "after", "desk", "camera", "cam-a", 1, T0 + 18 * DAY)
    out = governance.sweep(db, T0 + 26 * DAY)
    assert out["consolidated"] == 1 and "not_consolidated" not in out
    assert out["warnings"][0].startswith("Consolidated readings are at 87 % of their limit (7 of 8)")


def test_from_four_fifths_of_the_limit_the_owner_is_warned(instance):
    db, _ = instance
    teach(db, rounds=3)                                      # 9 events
    limited(db, 12)                                          # 9 of 12: 75 %, below the warning share
    out = governance.sweep(db, T0 + 8 * DAY)
    assert out["consolidated"] == 9 and "warnings" not in out
    assert "warnings" not in store.regulation(db) and "warnings" not in store.inventory(db)
    limited(db, 11, T0 + 8 * DAY + 1)                        # 9 of 11: 81 %
    near = "Consolidated readings are at 81 % of their limit (9 of 11)"
    for place in (store.regulation(db), store.inventory(db)):
        assert len(place["warnings"]) == 1 and place["warnings"][0].startswith(near)
        assert "At the limit a learning event that reaches the end of its lifetime leaves nothing" in \
            place["warnings"][0]
    assert store.consolidation_warnings(db) == store.regulation(db)["warnings"]
    limited(db, 0, T0 + 8 * DAY + 2)                         # no limit: nothing to warn about
    assert store.consolidation_warnings(db) == [] and store.consolidation_room(db) is None


def test_lowering_the_limit_below_what_is_kept_deletes_nothing_and_stops_consolidation(instance):
    db, _ = instance
    teach(db)
    assert governance.sweep(db, T0 + 8 * DAY)["consolidated"] == 18
    kept, before = folded(db), held(db, "desk")
    limited(db, 5, T0 + 8 * DAY + 1)
    assert folded(db) == kept and held(db, "desk") == before
    assert store.consolidation_room(db) == 0
    assert store.regulation(db)["warnings"][0].startswith("The limit on consolidated readings is reached (18 of 5)")
    obs(db, "late", "desk", "word", "ana", 0, T0 + 9 * DAY)
    out = governance.sweep(db, T0 + 17 * DAY)
    assert out["expired"] == 1 and out["not_consolidated"] == 1 and "consolidated" not in out
    assert folded(db) == kept and held(db, "desk") == before


def test_without_a_limit_everything_is_kept(instance):
    db, _ = instance
    limited(db, 0)
    teach(db)
    out = governance.sweep(db, T0 + 8 * DAY)
    assert out["consolidated"] == 18 and "not_consolidated" not in out and "warnings" not in out


def test_with_consolidation_off_the_limit_says_nothing(instance):
    db, _ = instance
    teach(db, rounds=2)
    limited(db, 6)
    governance.sweep(db, T0 + 8 * DAY)
    store.set_config(db, {"consolidation": False}, T0 + 8 * DAY + 1)
    assert store.consolidation_warnings(db) == [] and "warnings" not in store.regulation(db)
    obs(db, "late", "desk", "word", "ana", 0, T0 + 9 * DAY)
    out = governance.sweep(db, T0 + 17 * DAY)
    assert out["expired"] == 1 and "not_consolidated" not in out and "warnings" not in out


def test_a_copy_of_a_sample_counts_towards_the_limit_like_any_reading(instance):
    """The limit counts rows of the file, not learning: every expired copy of a sample leaves its own reading
    (which one counts is decided when beliefs are read), so each takes a place."""
    db, _ = instance
    teach(db, rounds=1)                                      # 3 events
    obs(db, "s-1", "desk", "word", "ana", 0, T0 + 20, sample="s")
    obs(db, "s-2", "desk", "word", "ana", 0, T0 + 21, sample="s")
    limited(db, 4)
    out = governance.sweep(db, T0 + 8 * DAY)
    assert out["consolidated"] == 4 and out["not_consolidated"] == 1
    assert [row[5] for row in folded(db)] == [None, None, None, "s"]     # the first copy got the last place


def test_the_executive_and_forgets_are_untouched_by_a_full_limit(instance):
    db, _ = instance
    ids = teach(db)
    limited(db, 9)
    governance.sweep(db, T0 + 8 * DAY)
    assert executive.decide(db, "desk", T0 + 8 * DAY + 1)["decision"] in ("act", "test_reference", "observe", "ask")
    assert governance.forget_evidence(db, ids[0], T0 + 8 * DAY + 2)["status"] == "forgotten"      # a kept reading
    assert governance.forget_evidence(db, ids[17], T0 + 8 * DAY + 3)["status"] == "absent"        # left nothing
    assert count(db, "evidence_consolidated") == 8
    assert store.consolidation_room(db) == 1                 # the forget made room again


# ---- the largest epoch -----------------------------------------------------------------------------------------------

def test_regulation_names_the_largest_epoch_and_what_its_beliefs_cost(instance):
    db, _ = instance
    assert "largest_epoch" not in store.regulation(db, measure=True)     # absent while nothing is consolidated
    teach(db, "desk", rounds=2)
    teach(db, "lab", now=T0 + 100, rounds=4)
    assert "largest_epoch" not in store.regulation(db, measure=True)
    governance.sweep(db, T0 + 8 * DAY)
    assert "largest_epoch" not in store.regulation(db)       # only when asked: the console header never pays for it
    largest = store.regulation(db, measure=True)["largest_epoch"]
    assert largest["epoch"] == "lab" and largest["consolidated_readings"] == 12
    assert isinstance(largest["compute_ms"], float) and 0 < largest["compute_ms"] < 5000
    version = store.state_version(db)
    store.regulation(db, measure=True)
    assert store.state_version(db) == version                # reading it writes nothing


def test_the_largest_epoch_is_reported_even_when_its_beliefs_cannot_be_computed(instance, monkeypatch):
    db, _ = instance
    teach(db, rounds=1)
    governance.sweep(db, T0 + 8 * DAY)

    def refuse(db, epoch):
        raise ValidationError("more mechanism segments than the model enumerates")
    monkeypatch.setattr(beliefs, "compute", refuse)
    assert store.regulation(db, measure=True)["largest_epoch"] == {"epoch": "desk", "consolidated_readings": 3,
                                                                   "compute_ms": None}


# ---- a restore --------------------------------------------------------------------------------------------------------

def test_a_restore_carries_the_limit_into_the_copy(instance, tmp_path):
    db, path = instance
    teach(db)
    archive = checkpoints.create(db, path, tmp_path / "cps", T0 + 50)    # the limit as it was born: a million
    limited(db, 4, T0 + 60)
    report = checkpoints.restore(db, archive["id"], tmp_path / "copy.sqlite", T0 + 70)
    assert report["settings_carried"] == {"max_consolidated_rows": {"from": 1_000_000, "to": 4}}
    with closing(store.open_existing(tmp_path / "copy.sqlite")) as copy:
        assert store.config(copy).max_consolidated_rows == 4
        out = governance.sweep(copy, T0 + 8 * DAY)           # the copy honours the owner's limit
        assert out["consolidated"] == 4 and out["not_consolidated"] == 14
    plain = checkpoints.restore(db, archive["id"], tmp_path / "plain.sqlite", T0 + 71, apply_tombstones=False)
    assert "settings_carried" not in plain or plain["settings_carried"] == {}
    with closing(store.open_existing(tmp_path / "plain.sqlite")) as copy:
        assert store.config(copy).max_consolidated_rows == 1_000_000     # a plain copy keeps the archive's


def test_a_damaged_live_limit_is_not_carried(instance, tmp_path):
    db, path = instance
    teach(db)
    limited(db, 9, T0 + 40)
    archive = checkpoints.create(db, path, tmp_path / "cps", T0 + 50)
    with store.write(db):
        raw = json.loads(db.execute("SELECT value FROM meta WHERE key='config'").fetchone()[0])
        raw["max_consolidated_rows"] = "plenty"
        db.execute("UPDATE meta SET value=? WHERE key='config'", (json.dumps(raw),))
    report = checkpoints.restore(db, archive["id"], tmp_path / "copy.sqlite", T0 + 70)
    assert report["settings_carried"] == {}
    assert any("max_consolidated_rows" in w and "was not carried" in w for w in report["settings_warnings"])
    with closing(store.open_existing(tmp_path / "copy.sqlite")) as copy:
        assert store.config(copy).max_consolidated_rows == 9


def test_a_restore_reads_from_the_live_file_only_the_readings_of_the_archives_own_events(instance, tmp_path):
    """The live file may hold every reading the individual ever kept; an archive can turn into readings only the
    events its own journal holds. The replay's result is the same, and what it holds in memory is bounded by the
    archive's journal."""
    db, path = instance
    teach(db, "old", rounds=4)                               # 12 events, consolidated before the archive exists
    governance.sweep(db, T0 + 8 * DAY)
    assert count(db, "evidence_consolidated") == 12
    ids = teach(db, "desk", now=T0 + 9 * DAY, rounds=2)      # 6 events in the journal when the archive is made
    archive = checkpoints.create(db, path, tmp_path / "cps", T0 + 9 * DAY + 50)
    governance.sweep(db, T0 + 17 * DAY)                      # they expire and are kept in the live file
    governance.forget_evidence(db, ids[0], T0 + 17 * DAY + 1)
    assert count(db, "evidence_consolidated") == 17
    asked = []
    read = checkpoints._held_readings

    def spy(live, wanted):
        held = read(live, wanted)
        asked.append((None if wanted is None else list(wanted), sorted(held)))
        return held
    checkpoints_held = checkpoints._held_readings
    try:
        checkpoints._held_readings = spy
        report = checkpoints.restore(db, archive["id"], tmp_path / "copy.sqlite", T0 + 17 * DAY + 2)
    finally:
        checkpoints._held_readings = checkpoints_held
    assert asked == [(list(range(13, 19)), list(range(14, 19)))]         # six journal seqs asked for, five held
    assert report["evidence_consolidated"] == 5 and report["evidence_removed"] == 6
    with closing(store.open_existing(tmp_path / "copy.sqlite")) as copy:
        assert [row[0] for row in folded(copy)] == list(range(1, 13)) + list(range(14, 19))
        assert count(copy, "evidence") == 0
        assert held(copy, "desk") == held(db, "desk") and held(copy, "old") == held(db, "old")


def test_reading_some_readings_gives_the_same_rows_as_reading_all(instance, monkeypatch):
    db, _ = instance
    teach(db, rounds=6)
    governance.sweep(db, T0 + 8 * DAY)
    everything = checkpoints._held_readings(db, None)
    assert len(everything) == 18
    monkeypatch.setattr(checkpoints, "READING_BATCH", 4)     # several statements
    assert checkpoints._held_readings(db, array("q", range(1, 19))) == everything
    some = checkpoints._held_readings(db, array("q", [2, 3, 11, 40, 41]))
    assert some == {seq: everything[seq] for seq in (2, 3, 11)}
    assert checkpoints._held_readings(db, array("q")) == {}
    forgets = checkpoints._forgets(db, T0 + 8 * DAY + 1, array("q", [5]))
    assert list(forgets["consolidated"]) == [5] and len(forgets["tombstones"]) == 18 and not db.in_transaction


def test_the_journal_seqs_of_a_copy_are_read_before_it_is_migrated(instance, tmp_path):
    db, path = instance
    teach(db, rounds=1)
    archive = checkpoints.create(db, path, tmp_path / "cps", T0 + 50)
    checkpoints.restore(db, archive["id"], tmp_path / "copy.sqlite", T0 + 60, apply_tombstones=False)
    with closing(sqlite3.connect(tmp_path / "copy.sqlite", isolation_level=None)) as copy:
        seqs = checkpoints._journal_seqs(copy)
        assert isinstance(seqs, array) and seqs.typecode == "q" and list(seqs) == [1, 2, 3]


# ---- the command line ----------------------------------------------------------------------------------------------

def test_cli_sets_shows_and_reports_the_limit(tmp_path, capsys):
    from neuroscifi import cli

    def nsf(*argv):
        code = cli.main(list(argv))
        captured = capsys.readouterr()
        return code, json.loads(captured.out) if code == 0 else captured.err

    path = str(tmp_path / "cli.sqlite")
    code, born = nsf("init", "--db", path, "--ttl-days", "0.000001", "--max-consolidated-rows", "2")
    assert code == 0
    code, shown = nsf("config", "--db", path)
    assert code == 0 and shown["config"]["max_consolidated_rows"] == 2 and shown["changed"] == []
    assert nsf("epoch", "start", "e", "--db", path)[0] == 0
    for k in range(3):
        assert nsf("observe", "e", f"x{k}", "camera", "0", "0", "cam", "--db", path)[0] == 0
    import time
    time.sleep(0.2)
    assert nsf("sweep", "--db", path)[0] == 0                # (each command above swept what had expired by then)
    code, regulation = nsf("regulation", "--db", path)
    assert code == 0 and regulation["consolidated_readings"] == 2
    assert regulation["warnings"][0].startswith("The limit on consolidated readings is reached (2 of 2)")
    assert regulation["largest_epoch"]["epoch"] == "e" and regulation["largest_epoch"]["consolidated_readings"] == 2
    code, birth = nsf("birth", "--db", path)
    assert code == 0 and birth["warnings"] == regulation["warnings"]
    code, changed = nsf("config", "--db", path, "--max-consolidated-rows", "0")
    assert code == 0 and changed["changed"] == ["max_consolidated_rows"] and "warnings" not in changed
    with pytest.raises(SystemExit):
        cli.main(["config", "--db", path, "--max-consolidated-rows", "-3"])
    assert "expected an integer from 0" in capsys.readouterr().err
    code, near = nsf("config", "--db", path, "--max-consolidated-rows", "2")
    assert code == 0 and near["warnings"][0].startswith("The limit on consolidated readings is reached (2 of 2)")
