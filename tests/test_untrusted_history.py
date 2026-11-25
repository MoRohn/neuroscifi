"""review1 S2/S4: the internal-state history is a file another account can write (two accounts: the folder 2770, the
file 0660). A number that is not finite, a sample dated in the future, impossible values or a file another account
created are ignored; the forecast fails open (it may only add a hold, a broken history adds none); the record, `nsf
internal-state`, the self-description and the research worker's claim never raise on it."""
from contextlib import closing
import json
import os
import time

import pytest

from neuroscifi import internal_state, self_description as sd
from neuroscifi.instance import store
from neuroscifi.resources import forecast, policy, shared
from neuroscifi.webcortex import orchestrator, scheduler, state

GIB = 1024 ** 3


@pytest.fixture
def individual(tmp_path):
    now = time.time()
    path = tmp_path / "individual.sqlite"
    with closing(store.create(path, now - 100)) as db:
        state.set_config(db, {"enabled": True}, now)
        orchestrator.submit(db, "Where does the okapi live?", now, origin="console")
    folder = internal_state.folder(path)
    folder.mkdir(mode=0o700)
    yield path, folder / internal_state.HISTORY_FILE, now


def lines(rows):
    return "".join(r + "\n" for r in rows)


FORGED = {
    "nan": '{"v":1,"at":%f,"available_bytes":NaN,"total_bytes":16000000000,"background_running":0}',
    "infinity": '{"v":1,"at":%f,"available_bytes":1000,"total_bytes":Infinity,"background_running":0}',
    "huge": '{"v":1,"at":%f,"available_bytes":1e400,"total_bytes":16000000000,"background_running":0}',
}


@pytest.mark.parametrize("kind", sorted(FORGED))
def test_a_non_finite_number_never_breaks_a_reader(individual, kind):
    path, history, now = individual
    history.write_text(lines(FORGED[kind] % (now - 30 * (5 - i)) for i in range(5)))
    with closing(store.connect(path)) as db:
        snap = internal_state.snapshot(db, str(path), now)
        assert snap["history"]["samples"] == 0 and snap["history"]["ignored_samples"] == 5
        assert snap["forecast"]["hold"] is False
        record = sd.build(db, path, now)
        assert sd.value(record, "internal_state")["history"]["ignored_samples"] == 5
        sd.build(db, path, now, scope="family", family="x")
        store.set_config(db, {"predictive_regulation": True}, now)
        policy._predictive_cache.clear()
        assert policy.background_gate(str(path))["why"] != "forecast"
        assert scheduler.claim_next(db, "h1", now + 1)               # the owner's task is still claimed


def test_samples_dated_in_the_future_or_impossible_are_ignored(individual):
    path, history, now = individual
    falling = [{"v": 1, "at": now + 3600 + 30 * i, "available_bytes": int((8 - i) * GIB), "total_bytes": 16 * GIB,
                "background_running": 0} for i in range(6)]            # a "leak" dated an hour ahead
    impossible = [{"v": 1, "at": now - 30 * (6 - i), "available_bytes": 32 * GIB, "total_bytes": 16 * GIB,
                   "background_running": 0} for i in range(6)]          # more available than in total
    history.write_text(lines(json.dumps(s) for s in falling + impossible))
    samples = internal_state.history(str(path), now=now)
    assert [s["at"] for s in samples] == [s["at"] for s in impossible]   # the future ones are not read at all
    assert forecast.assess(samples, now=now)["forecast"] is None         # nor are impossible ones used
    assert forecast.assess(falling, now=now)["hold"] is False and forecast.assess(falling, now=now)["forecast"] is None


def test_the_forecast_fails_open(monkeypatch):
    monkeypatch.setattr(forecast, "_assess", lambda *a, **k: 1 / 0)
    verdict = forecast.assess([{"at": 1.0}], now=2.0)
    assert verdict["hold"] is False and "could not be read" in verdict["note"]


def test_a_history_file_another_account_created_is_not_read(individual, monkeypatch):
    path, history, now = individual
    history.write_text(lines(json.dumps({"v": 1, "at": now - 30 * (4 - i), "available_bytes": 4 * GIB,
                                         "total_bytes": 16 * GIB, "background_running": 0}) for i in range(4)))
    assert len(internal_state.history(str(path), now=now)) == 4
    monkeypatch.setattr(shared, "writers", lambda db_path: {os.getuid() + 12345})
    assert internal_state.history(str(path), now=now) == []
    with closing(store.connect(path)) as db:
        snap = internal_state.snapshot(db, str(path), now)
    assert snap["history"]["samples"] == 0 and "another account" in snap["history"]["unreadable"]


def test_shared_loads_refuses_numbers_that_are_not_finite():
    for text in ('{"x": NaN}', '{"x": Infinity}', '{"x": -Infinity}', '{"x": 1e400}'):
        with pytest.raises(ValueError):
            shared.loads(text)
    assert shared.loads('{"x": 1.5, "y": 2}') == {"x": 1.5, "y": 2}


# ---- review1b R1: an integer too large for a float ------------------------------------------------------------------

BIG = "1" + "0" * 400


def test_shared_loads_refuses_integers_too_large():
    for text in ('{"x": %s}' % BIG, '{"x": -%s}' % BIG, '[1234567890123456789]'):
        with pytest.raises(ValueError):
            shared.loads(text)
    assert shared.loads('{"x": 123456789012345678, "y": -123456789012345678}') == {
        "x": 123456789012345678, "y": -123456789012345678}


BIG_HISTORY = {
    "at": '{"v":1,"at":%s,"available_bytes":4000000000,"total_bytes":16000000000,"background_running":0}' % BIG,
    "available": '{"v":1,"at":%f,"available_bytes":' + BIG + ',"total_bytes":16000000000,"background_running":0}',
}


@pytest.mark.parametrize("kind", sorted(BIG_HISTORY))
def test_a_huge_integer_in_the_history_never_breaks_a_reader(individual, kind):
    path, history, now = individual
    row = BIG_HISTORY[kind]
    history.write_text(lines((row % (now - 30 * (5 - i))) if "%f" in row else row for i in range(5)))
    with closing(store.connect(path)) as db:
        snap = internal_state.snapshot(db, str(path), now)                  # `nsf internal-state`
        assert snap["history"]["samples"] == 0 and snap["history"]["ignored_samples"] == 5
        store.set_config(db, {"predictive_regulation": True}, now)
        policy._predictive_cache.clear()
        assert policy.background_gate(str(path))["allowed"] is True
        assert scheduler.claim_next(db, "h1", now + 1)               # the owner's task is still claimed


def test_a_huge_integer_the_parser_let_through_is_never_compared(individual, monkeypatch):
    """The readers' own guard, apart from shared.loads: a Python int no float can hold is not a number to them."""
    from neuroscifi.presence import activity
    from neuroscifi.voice import beacon
    assert activity._number(int(BIG)) is False and beacon._number(int(BIG)) is False
    assert activity._number(12) is True and beacon._number(1.5) is True
    path, history, now = individual
    monkeypatch.setattr(shared, "loads", json.loads)                   # a parser with no bound on integers
    history.write_text(lines([BIG_HISTORY["at"]] * 3))
    assert internal_state.history(str(path), now=now) == []


def test_a_beacon_with_a_huge_integer_leaves_the_worker_working(individual):
    from neuroscifi import resources
    path, _, now = individual
    beacon = policy._beacon_path(str(path))
    beacon.write_text('{"at": %s, "active": true, "session": true}' % BIG)
    assert policy.audio_active(str(path)) is False
    assert policy.voice_session_active(str(path)) is False
    assert policy.background_gate(str(path))["allowed"] is True
    assert resources.status(str(path), full=False)["level"]
    with closing(store.connect(path)) as db:
        assert scheduler.claim_next(db, "h1", now + 1)               # the owner's task is still claimed


def test_the_background_gate_fails_open_when_the_audio_check_raises(individual, monkeypatch):
    path, _, _ = individual
    monkeypatch.setattr(policy, "audio_active", lambda *a, **k: 1 / 0)
    gate = policy.background_gate(str(path), sample={"level": "ok", "reasons": []})
    assert gate["allowed"] is True and gate["audio"] is False


def test_a_beacon_another_account_created_is_not_read(individual, monkeypatch):
    path, _, _ = individual
    beacon = policy._beacon_path(str(path))
    beacon.write_text(json.dumps({"at": time.time(), "active": True, "session": True}))
    assert policy.audio_active(str(path)) is True
    monkeypatch.setattr(shared, "writers", lambda db_path: {os.getuid() + 12345})
    assert policy.audio_active(str(path)) is False


def test_owner_presence_survives_a_huge_integer_in_another_process_file(individual):
    from neuroscifi.presence import activity, aggregator
    path, _, now = individual
    started = activity.process_started(1)
    folder = internal_state.folder(path)
    for text in ('{"pid":1,"started":%s,"at":%s,"entries":[]}' % (started, BIG),
                 '{"pid":1,"started":%s,"at":%f,"entries":[{"activity":"call","since":%s}]}' % (started, now, BIG)):
        (folder / "inflight-1.json").write_text(text)
        assert activity.remote(str(path), now) == []
        with closing(store.connect(path)) as db:
            assert aggregator.derive(db, str(path), now) is not None


def test_owner_presence_survives_a_reader_of_other_processes_that_raises(individual, monkeypatch):
    from neuroscifi.presence import activity, aggregator
    path, _, now = individual
    monkeypatch.setattr(activity, "remote", lambda *a, **k: 1 / 0)
    with closing(store.connect(path)) as db:
        assert aggregator.derive(db, str(path), now) is not None
