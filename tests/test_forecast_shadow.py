"""Predictive regulation in shadow (Wave 3 lane H; ADR-058): the forecast's verdict is recorded where the gate would
consult it and settled against what memory did; the gate decides exactly as with the setting off.

The staged wiring (acceptance/cognition/shadow_check.py) is test_predictive_wiring.py's: each tick of an
anticipation trace is staged on the host's memory sample and the research queue, the worker's sampler
(internal_state.record) appends it to the history file and settles the shadow verdicts, and the gate decides."""
from contextlib import closing
import json
import logging
import os
import sqlite3
import time

import pytest

from neuroscifi.acceptance.cognition import anticipation, shadow_check, splits
from neuroscifi.instance import store
from neuroscifi.resources import forecast, monitor, policy, shadow, shared
from neuroscifi.webcortex import state

T0 = shadow_check.T0
GIB = 1024 ** 3
CRITICAL_GIB = monitor.thresholds(int(anticipation.TOTAL_GIB * GIB))["critical_below_bytes"] / GIB
FORECAST_TICKS = int(forecast.HORIZON_SECONDS / anticipation.TICK_SECONDS)


def spec(name, split="development"):
    return next(t for t in splits.ANTICIPATION[split]["traces"] if t["name"] == name)


def run(directory, setting, name="fast memory leak", seed=11, split="development", **kwargs):
    return shadow_check.staged_run(directory, spec(name, split), seed, setting, **kwargs)


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    monkeypatch.setattr(shadow, "_failures", {})
    policy._predictive_cache.clear()


def test_shadow_decides_exactly_as_off_and_records_one_verdict_per_sample(tmp_path):
    off = run(tmp_path / "off", False)
    shadowed = run(tmp_path / "shadow", "shadow")
    assert [t["gate"] for t in shadowed["ticks"]] == [t["gate"] for t in off["ticks"]]
    assert not [t for t in shadowed["ticks"] if t["gate"]["why"] == "forecast"]
    assert shadowed["observations"] == off["observations"]          # the plant's trace is the same, tick by tick
    assert any(t["forecast_hold"] for t in shadowed["ticks"]), "the forecast never held: nothing was shown"
    assert not (tmp_path / "off" / "individual.sqlite.resources" / shadow.FILE).exists()
    out = shadow.report(shadowed["path"], shadowed["clock"])
    assert out["verdicts"]["verdicts"] == anticipation.TICKS - out["pending"]
    assert out["problems"] == [] and out["ignored_lines"] == 0


def test_with_the_setting_on_the_forecast_still_holds(tmp_path):
    """The third value leaves the second as it was."""
    on = run(tmp_path, True)
    assert [t for t in on["ticks"] if t["gate"]["why"] == "forecast"]
    assert not (tmp_path / "individual.sqlite.resources" / shadow.FILE).exists()


@pytest.mark.parametrize("name", ["fast memory leak", "slow memory leak, then release", "periodic voice sessions",
                                  "regime shift to a tighter level"])
def test_incremental_settlement_equals_the_family_scoring(tmp_path, name):
    """The sampler settles every SETTLE_EVERY s of the staged clock, with the product's definitions (the critical
    line, each verdict's horizon); the report must say what scoring.breach_report says of the same verdicts."""
    r = run(tmp_path, "shadow", name=name)
    out = shadow.report(r["path"], r["clock"])
    assert (tmp_path / "individual.sqlite.resources" / shadow.DAYS_FILE).exists()      # the sampler settled
    assert shadow_check.mismatches(out, shadow_check.reference(r, floor_gib=CRITICAL_GIB,
                                                               horizon_ticks=FORECAST_TICKS)) == {}


@pytest.mark.parametrize("name,split", [("fast memory leak", "development"),
                                        ("slow memory leak, then release", "development"),
                                        ("two leaks of different speed", "held_out"),
                                        ("spike on top of an elevated level", "held_out")])
def test_lazy_settlement_with_the_family_definitions_equals_the_family_scoring(tmp_path, name, split):
    """Nothing settled during the run; the report settles everything with the family's breach line and horizon."""
    r = run(tmp_path, "shadow", name=name, split=split, settle_every=float("inf"))
    out = shadow.report(r["path"], r["clock"], window=anticipation.HORIZON * anticipation.TICK_SECONDS,
                        floor_bytes=int(anticipation.BREACH_FLOOR_GIB * GIB))
    ref = shadow_check.reference(r, floor_gib=anticipation.BREACH_FLOOR_GIB, horizon_ticks=anticipation.HORIZON)
    assert shadow_check.mismatches(out, ref) == {}


def test_the_settlement_check_can_fail(tmp_path):
    """The comparison is not vacuous: one hold changed in the reference is seen."""
    r = run(tmp_path, "shadow", name="fast memory leak")
    out = shadow.report(r["path"], r["clock"])
    flipped = dict(r, ticks=[dict(t, forecast_hold=not t["forecast_hold"]) if t["tick"] == 100 else t
                             for t in r["ticks"]])
    assert shadow_check.mismatches(out, shadow_check.reference(flipped, floor_gib=CRITICAL_GIB,
                                                               horizon_ticks=FORECAST_TICKS))


@pytest.mark.parametrize("mode", ["raise", "timeout", "garbage-none", "garbage-text", "garbage-hold",
                                  "garbage-numbers"])
def test_a_failing_forecast_changes_no_decision_and_is_visible(tmp_path, monkeypatch, caplog, mode):
    def broken(*a, **k):
        if mode == "raise":
            raise ZeroDivisionError("SUBJECT-MARKER-77")
        if mode == "timeout":
            raise TimeoutError("SUBJECT-MARKER-77")
        return {"garbage-none": None, "garbage-text": "SUBJECT-MARKER-77",
                "garbage-hold": {"hold": "yes", "forecast": {}},
                "garbage-numbers": {"hold": True, "forecast": {"horizon_seconds": "x"}}}[mode]
    monkeypatch.setattr(forecast, "assess", broken)
    off = run(tmp_path / "off", False)
    with caplog.at_level(logging.WARNING):
        shadowed = run(tmp_path / "shadow", "shadow")
    assert [t["gate"] for t in shadowed["ticks"]] == [t["gate"] for t in off["ticks"]]
    assert any("shadow" in r.getMessage() for r in caplog.records)
    assert not any("SUBJECT-MARKER" in r.getMessage() for r in caplog.records)
    out = shadow.report(shadowed["path"], shadowed["clock"])
    assert out["verdicts"]["failed"] > 0 and out["verdicts"]["holds"] == 0


def test_a_shadow_file_that_is_not_a_file_changes_nothing_and_is_visible(tmp_path, caplog):
    folder = tmp_path / "shadow" / "individual.sqlite.resources"
    folder.mkdir(parents=True)
    os.mkfifo(folder / shadow.FILE)
    off = run(tmp_path / "off", False)
    with caplog.at_level(logging.WARNING):
        shadowed = run(tmp_path / "shadow", "shadow")
    assert [t["gate"] for t in shadowed["ticks"]] == [t["gate"] for t in off["ticks"]]
    # a FIFO opened for writing without a reader: ENXIO, before the plain-file check could refuse it
    assert any("shadow" in r.getMessage() and ("NotAFile" in r.getMessage() or "ENXIO" in r.getMessage())
               for r in caplog.records)
    assert shadow.report(shadowed["path"], shadowed["clock"])["problems"]


def test_the_record_is_bounded_and_holds_numbers_and_fixed_words_only(tmp_path, monkeypatch):
    r = run(tmp_path, "shadow")
    folder = tmp_path / "individual.sqlite.resources"
    lines = (folder / shadow.FILE).read_text().splitlines()
    # the sampler settled every SETTLE_EVERY s: only the verdicts since the last pass and their horizon are kept
    assert len(lines) <= (shadow.SETTLE_EVERY + forecast.HORIZON_SECONDS) / anticipation.TICK_SECONDS + 1
    days = json.loads((folder / shadow.DAYS_FILE).read_text())
    assert set(days) == {"schema", "v", "settled_until", "samples_until", "days"} and len(days["days"]) >= 1
    for line in lines:
        item = json.loads(line)
        assert set(item) <= {"v", "at", "gate", "hold", "why", "horizon_seconds", "margin_bytes", "sample_at",
                             "failed"}
        assert all(isinstance(v, (int, float, bool, type(None))) or v in shadow.GATES + shadow.WHYS
                   for v in item.values())
    # a full file takes no more lines, and the gate is unchanged
    monkeypatch.setattr(shadow, "MAX_BYTES", 10)
    monkeypatch.setattr(shadow, "_seen", {})
    before = (folder / shadow.FILE).read_text()
    assert shadow.observe(r["path"], r["clock"], None) is None
    assert (folder / shadow.FILE).read_text() == before


def test_forged_lines_are_ignored_and_nothing_is_counted_twice(tmp_path):
    r = run(tmp_path, "shadow")
    folder = tmp_path / "individual.sqlite.resources"
    first = shadow.report(r["path"], r["clock"])
    with open(folder / shadow.FILE, "a") as fh:
        fh.write("[" * 100000 + "\n")
        fh.write('{"v":1,"at":1e400,"gate":"allowed","hold":true}\n')
        fh.write('{"v":1,"at":' + "9" * 400 + ',"gate":"allowed"}\n')
        fh.write('{"v":1,"at":5,"gate":"nope","hold":false}\n')
        fh.write('{"v":1,"at":5,"gate":"allowed","hold":true,"why":"memory","horizon_seconds":1e9,"sample_at":5}\n')
        fh.write('{"v":1,"at":' + str(r["clock"] + 1e6) + ',"gate":"allowed","hold":null}\n')
    again = shadow.report(r["path"], r["clock"])
    assert again["ignored_lines"] == 6
    assert again["settled"] == first["settled"] and again["breaches"] == first["breaches"]
    # a duplicate verdict for an already settled sample (a second process, a restart) is not counted
    with open(folder / shadow.FILE, "a") as fh:
        fh.write(json.dumps({"v": 1, "at": T0 + 30.5, "gate": "allowed", "hold": True, "why": "memory",
                             "horizon_seconds": 180.0, "sample_at": T0 + 30.0}) + "\n")
    # a settlement pass now (the sampler), then a report: the same numbers
    shadow._settled_at.clear()
    shadow.settle_due(r["path"], r["clock"])
    third = shadow.report(r["path"], r["clock"])
    assert third["settled"] == first["settled"] and third["breaches"] == first["breaches"]
    assert third["verdicts"] == first["verdicts"]


def test_a_forged_roll_up_is_read_bounded(tmp_path):
    r = run(tmp_path, "shadow")
    folder = tmp_path / "individual.sqlite.resources"
    (folder / shadow.DAYS_FILE).write_text(json.dumps({"days": {"2027-01-15": {"hits": -5, "misses": True,
                                                                               "lead_seconds_max": "x"},
                                                                "not a day": {"hits": 3}}}))
    out = shadow.report(r["path"], r["clock"])
    assert out["settled"]["hits"] >= 0 and "not a day" not in out["days"]
    (folder / shadow.DAYS_FILE).write_text("[" * 100000)
    assert "could not be read" in " ".join(shadow.report(r["path"], r["clock"])["problems"])


def test_another_accounts_files_are_not_read(tmp_path, monkeypatch):
    r = run(tmp_path, "shadow")
    monkeypatch.setattr(shared, "writers", lambda db_path: {os.getuid() + 12345})
    out = shadow.report(r["path"], r["clock"])
    assert any("another account" in p for p in out["problems"])


def test_in_a_shared_folder_the_files_are_read_only_for_the_other_account(tmp_path):
    folder = tmp_path / "individual.sqlite.resources"
    folder.mkdir(mode=0o700)
    os.chmod(folder, 0o2770)
    run(tmp_path, "shadow")
    for name in (shadow.FILE, shadow.DAYS_FILE):
        assert (os.stat(folder / name).st_mode & 0o777) == shared.SHARED_READ_ONLY_FILE


def test_a_status_display_records_no_verdict(tmp_path):
    from neuroscifi import internal_state
    from neuroscifi.resources import status
    path = tmp_path / "individual.sqlite"
    db = store.create(path, T0)
    try:
        store.set_config(db, {"predictive_regulation": "shadow"}, T0)
        internal_state.snapshot(db, str(path))
        status(str(path), full=False)
    finally:
        db.close()
    assert not (tmp_path / "individual.sqlite.resources" / shadow.FILE).exists()
    policy.background_gate(str(path), sample={"level": "normal", "reasons": []}, audio=False)
    assert (tmp_path / "individual.sqlite.resources" / shadow.FILE).exists()


def test_the_setting_reads_validates_and_restores(tmp_path):
    from neuroscifi.instance import checkpoints
    path = tmp_path / "individual.sqlite"
    db = store.create(path, T0)
    try:
        store.set_config(db, {"predictive_regulation": "shadow"}, T0)
        assert store.config(db).predictive_regulation == "shadow"
        assert store.setting_problems(db) == []
        assert policy.predictive_mode(str(path)) == "shadow"
        assert policy.predictive_on(str(path)) is False and policy.predictive_shadow(str(path)) is True
        assert "predictive_regulation" in checkpoints.CARRIED_SETTINGS and store.valid_setting(
            "predictive_regulation", "shadow")
        for bad in ("on", "Shadow", 1, None):
            with store.write(db):
                db.execute("UPDATE meta SET value=json_set(value,'$.predictive_regulation',?) WHERE key='config'",
                           (bad,))
            policy._predictive_cache.clear()
            assert store.config(db).predictive_regulation is False
            assert policy.predictive_mode(str(path)) is False
        with pytest.raises(Exception):
            store.set_config(db, {"predictive_regulation": "maybe"}, T0)
    finally:
        db.close()


def test_the_cli_sets_shadow_and_prints_the_report(tmp_path, capsys):
    from neuroscifi import cli
    path = tmp_path / "individual.sqlite"
    store.create(path, T0).close()
    assert cli.main(["config", "--db", str(path), "--predictive-regulation-shadow"]) in (0, None)
    with closing(sqlite3.connect(path)) as raw:
        assert json.loads(raw.execute("SELECT value FROM meta WHERE key='config'").fetchone()[0])[
            "predictive_regulation"] == "shadow"
    capsys.readouterr()
    assert cli.main(["internal-state", "--db", str(path), "--shadow-report"]) in (0, None)
    out = json.loads(capsys.readouterr().out)
    assert out["schema"] == shadow.SCHEMA and out["setting"] == "shadow" and out["enough_data"] is False
    assert "Not enough data" in out["statement"]
    assert cli.main(["config", "--db", str(path), "--no-predictive-regulation"]) in (0, None)


def test_enough_data_needs_the_stated_minimum(tmp_path):
    r = run(tmp_path, "shadow", name="fast memory leak")
    out = shadow.report(r["path"], r["clock"])
    assert out["rests_on"]["days_with_verdicts"] < shadow.MIN_DAYS and out["enough_data"] is False


def test_the_gate_cost_in_shadow_once_the_sample_is_recorded(tmp_path):
    """Plumbing timing, not a benchmark: once the newest sample's verdict is recorded the shadow adds a stat call."""
    r = run(tmp_path, "shadow")
    started = time.perf_counter()
    for _ in range(200):
        policy.background_gate(r["path"], sample={"level": "normal", "reasons": []}, audio=False)
    assert (time.perf_counter() - started) / 200 < 0.01


def test_quiet_and_enabled_settings_are_unaffected(tmp_path):
    """state is imported for the staged individual; the web config is untouched by the shadow setting."""
    path = tmp_path / "individual.sqlite"
    db = store.create(path, T0)
    try:
        before = state.get_config(db)
        store.set_config(db, {"predictive_regulation": "shadow"}, T0)
        assert state.get_config(db) == before
    finally:
        db.close()
