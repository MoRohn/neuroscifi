"""ADR-051 (lane I): the internal-state record, its persisted history and the predictive regulation that reads it
(resources/forecast.py through policy.background_gate)."""
import fcntl
import json
import os
import stat

import pytest

from neuroscifi import internal_state
from neuroscifi.instance import store
from neuroscifi.resources import forecast, monitor, policy
from neuroscifi.webcortex import orchestrator, scheduler, state

GIB = 1024 ** 3
T0 = 1_795_200_000.0


@pytest.fixture
def individual(tmp_path):
    path = tmp_path / "individual.sqlite"
    db = store.create(path, T0)
    try:
        yield db, str(path)
    finally:
        db.close()


def samples(points, *, step=30.0, total=128, running=0, audio=None):
    """History samples from available-memory readings (GiB), one every `step` s from T0."""
    return [{"v": 1, "at": T0 + i * step, "available_bytes": int(a * GIB), "total_bytes": total * GIB,
             "background_running": running if not callable(running) else running(i),
             "audio": bool(audio and audio(i))} for i, a in enumerate(points)]


# ---- the record ----------------------------------------------------------------------------------------------------

def test_every_field_says_what_it_measured_where_from_and_how_old(individual):
    db, path = individual
    out = internal_state.snapshot(db, path, T0)
    assert out["schema"] == "nsf.internal_state/1" and out["version"] == 1
    for name in ("memory", "audio_active", "background_gate", "background_model_slots", "model_services_in_use",
                 "writes", "research_queue", "worker", "model_calls", "knowledge_staleness", "prediction_errors",
                 "internal_disagreement", "quiet", "idle_unloaded"):
        field = out["fields"][name]
        assert set(field) == {"value", "source", "at", "age_seconds"} and field["source"], name
    assert out["fields"]["memory"]["value"]["available_bytes"] == 64 * GIB      # tests/conftest.py's steady host
    assert out["fields"]["writes"]["value"]["writes_allowed"] is True
    assert out["settings"] == {"internal_state_history": True, "predictive_regulation": False}
    assert out["forecast"]["forecast"] is None                                     # no history yet
    assert not os.path.exists(f"{path}.inference.sqlite")                          # the ledger is read, never made


def test_the_record_reads_held_locks_without_taking_one(individual):
    db, path = individual
    folder = internal_state.folder(path)
    folder.mkdir(mode=0o700)
    fd = os.open(folder / "model-slot-0.lock", os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert internal_state.snapshot(db, path, T0)["fields"]["background_model_slots"]["value"]["in_use"] == 1
        fcntl.flock(fd, fcntl.LOCK_UN)
        assert internal_state.snapshot(db, path, T0)["fields"]["background_model_slots"]["value"]["in_use"] == 0
        with policy.background_model_slot(path) as slot:          # nothing was left held by the reading
            assert slot == 0
    finally:
        os.close(fd)


def test_history_is_appended_bounded_private_and_only_numbers(individual, monkeypatch):
    db, path = individual
    monkeypatch.setattr(internal_state, "HISTORY_KEPT", 5)
    for i in range(12):
        assert internal_state.record(path, T0 + i)["at"] == T0 + i
    kept = internal_state.history(path)
    assert 5 <= len(kept) <= 10 and [s["at"] for s in kept] == [T0 + i for i in range(12 - len(kept), 12)]
    file = internal_state.folder(path) / internal_state.HISTORY_FILE
    assert stat.S_IMODE(os.stat(file).st_mode) == 0o600
    for line in file.read_text().splitlines():
        item = json.loads(line)
        assert all(isinstance(v, (int, float, bool, type(None))) or k == "level" for k, v in item.items())


def test_the_owner_can_turn_the_history_off(individual):
    db, path = individual
    store.set_config(db, {"internal_state_history": False}, T0)
    assert internal_state.record(path, T0) is None
    assert not (internal_state.folder(path) / internal_state.HISTORY_FILE).exists()


def test_a_damaged_history_line_is_skipped(individual):
    db, path = individual
    internal_state.record(path, T0)
    with open(internal_state.folder(path) / internal_state.HISTORY_FILE, "a") as fh:
        fh.write("not json\n[1,2]\n")
    internal_state.record(path, T0 + 30)
    assert [s["at"] for s in internal_state.history(path)] == [T0, T0 + 30]


def test_the_worker_records_a_sample_while_it_runs(individual):
    db, path = individual
    scheduler.work(path, once=True, idle=False)
    assert len(internal_state.history(path)) >= 1


def test_nsf_internal_state(individual, capsys):
    from neuroscifi.cli import main
    db, path = individual
    internal_state.record(path, T0)
    assert main(["internal-state", "--db", path, "--history", "1"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["schema"] == internal_state.SCHEMA and len(out["history"]["recent"]) == 1


# ---- the forecast --------------------------------------------------------------------------------------------------

def test_steady_memory_holds_nothing():
    verdict = forecast.assess(samples([60.0] * 20))
    assert verdict["hold"] is False and verdict["forecast"]["trend_bytes_per_minute"] == 0


def test_a_leak_toward_the_critical_line_holds_before_it_is_reached():
    leak = [30.0 - 1.0 * i for i in range(20)]                     # 1 GiB per 30 s; now 11 GiB, critical ~7.7 GiB
    verdict = forecast.assess(samples(leak))
    level, _ = monitor.pressure({"total_bytes": 128 * GIB, "available_bytes": int(leak[-1] * GIB)})
    assert level != "critical" and verdict["hold"] is True              # the reactive gate would still allow
    assert "forecast" in verdict["reason"] and "critical line" in verdict["reason"]
    assert verdict["forecast"]["trend_bytes_per_minute"] < 0
    assert forecast.assess(samples(leak), use_trend=False)["hold"] is False       # the trend is what anticipates


def test_the_footprint_of_background_work_is_read_from_the_history():
    points = [30.0] * 6 + [18.0] * 6 + [30.0] * 6                   # one task: 12 GiB while it ran
    hist = samples(points, running=lambda i: 1 if 6 <= i < 12 else 0)
    assert forecast.footprint(forecast._points(hist)) == pytest.approx(12 * GIB, rel=1e-6)
    verdict = forecast.assess(hist)
    assert verdict["hold"] is True                                   # 30 - 2 x 12 < 7.7 GiB
    assert forecast.assess(hist, use_footprint=False)["hold"] is False


def test_too_little_or_stale_history_says_nothing():
    assert forecast.assess(samples([10.0] * 3))["forecast"] is None
    assert forecast.assess(samples([9.0] * 10), now=T0 + 3600)["forecast"] is None


def test_a_regular_voice_pattern_is_anticipated():
    period = 40                                                        # a session every 20 min, 4 samples long
    hist = samples([60.0] * 115, audio=lambda i: i % period in range(4))    # sessions start at 40 and 80
    verdict = forecast.assess(hist, now=T0 + 114 * 30)                 # 3 min before the one expected at 120
    assert verdict["hold"] is True and "voice session is expected" in verdict["reason"]
    assert verdict["forecast"]["voice"]["period_seconds"] == 40 * 30
    assert forecast.assess(hist, now=T0 + 114 * 30, use_voice=False)["hold"] is False
    assert forecast.assess(hist[:100], now=T0 + 99 * 30)["hold"] is False        # not yet due
    irregular = samples([60.0] * 85, audio=lambda i: i in (2, 3, 10, 11, 60, 61))
    assert forecast.assess(irregular)["hold"] is False


# ---- the gate ------------------------------------------------------------------------------------------------------

def write_history(path, items):
    for item in items:
        internal_state.append(path, item)


def test_the_gate_ignores_the_forecast_until_the_owner_turns_it_on(individual, monkeypatch):
    db, path = individual
    import time
    now = time.time()
    leak = samples([30.0 - i for i in range(20)])
    for s in leak:
        s["at"] += now - leak[-1]["at"]
    write_history(path, leak)
    assert policy.background_gate(path)["allowed"] is True
    store.set_config(db, {"predictive_regulation": True}, now)
    gate = policy.background_gate(path)
    assert gate["allowed"] is False and gate["why"] == "forecast" and "predictive regulation" in gate["reason"]
    # it only adds a refusal: audio and critical pressure keep their own reasons
    assert policy.background_gate(path, audio=True)["why"] == "audio"
    store.set_config(db, {"predictive_regulation": False}, now)
    assert policy.background_gate(path)["allowed"] is True


def test_a_forecast_hold_never_holds_the_owners_work(individual):
    db, path = individual
    import time
    now = time.time()
    leak = samples([30.0 - i for i in range(20)])
    for s in leak:
        s["at"] += now - leak[-1]["at"]
    write_history(path, leak)
    store.set_config(db, {"predictive_regulation": True}, now)
    state.set_config(db, {"enabled": True}, now)
    owner = orchestrator.submit(db, "What is the latest Zephyr compiler release?", now, origin="cli")
    background = orchestrator.submit(db, "Zephyr compiler latest version", now - 1000, origin="curiosity")
    claimed = scheduler.claim_next(db, "w", now)
    assert claimed is not None and claimed[0] == owner["id"]
    assert scheduler.claim_next(db, "w", now) is None                  # the background task waits, queued
    assert orchestrator.get(db, background["id"])["status"] == "queued"


def test_the_gate_reads_the_setting_again_only_when_the_file_changed(individual, monkeypatch):
    """Wave 1 integration: the gate opened a read-only connection on every call (0.47 ms with the setting off). The
    value is reused while the file and its log are unchanged (and for at most PREDICTIVE_CACHE_SECONDS); a change of
    the setting is seen on the next call."""
    import sqlite3
    db, path = individual
    opened, real_connect = [], sqlite3.connect
    monkeypatch.setattr(sqlite3, "connect", lambda *a, **k: opened.append(a[0]) or real_connect(*a, **k))
    sample = {"level": "ok", "reasons": []}
    assert policy.background_gate(path, sample=sample, audio=False)["allowed"] is True
    for _ in range(50):
        assert policy.background_gate(path, sample=sample, audio=False)["allowed"] is True
    assert len(opened) <= 1
    store.set_config(db, {"predictive_regulation": True}, T0)
    assert policy.predictive_on(path) is True
    store.set_config(db, {"predictive_regulation": False}, T0)
    assert policy.predictive_on(path) is False
    monkeypatch.setattr(policy.time, "monotonic", lambda: 1e12)        # past the lifetime: read again
    before = len(opened)
    assert policy.predictive_on(path) is False and len(opened) == before + 1


def test_a_damaged_setting_reads_as_off(individual):
    db, path = individual
    with store.write(db):
        db.execute("UPDATE meta SET value=json_set(value,'$.predictive_regulation','yes') WHERE key='config'")
    assert policy.predictive_on(path) is False and store.config(db).predictive_regulation is False
    assert any("predictive_regulation" in p for p in store.setting_problems(db))
