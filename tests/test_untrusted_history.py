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
