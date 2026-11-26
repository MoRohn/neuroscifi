"""Wave 3 lane H (ADR-058), fault injection as lane U did it: predictive regulation in shadow must change nothing
when it fails, and its failure must show. Each case runs an anticipation trace (development, "fast memory leak",
seed 11) through the product's wiring (acceptance/cognition/shadow_check.py: the worker's sampler, the history file,
the real gate) with the setting off and with it "shadow" while the shadow part is made to raise, to time out, to
return garbage, or to meet a file it cannot use:

  same      the gate's decisions with shadow, tick by tick, equal those with the setting off
  visible   a WARNING log record names the kind of failure (never its message, which could name a subject), and,
            where the record could still be written, the report counts the failed verdicts
  fired     the faulty part was reached (otherwise the case proves nothing)"""
import logging
import os

import pytest

from neuroscifi.acceptance.cognition import shadow_check, splits
from neuroscifi.resources import forecast, policy, shadow

MARKER = "SUBJECT-MARKER-5858"
SPEC = next(t for t in splits.ANTICIPATION["development"]["traces"] if t["name"] == "fast memory leak")


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    monkeypatch.setattr(shadow, "_failures", {})
    policy._predictive_cache.clear()


def both(tmp_path, caplog):
    off = shadow_check.staged_run(tmp_path / "off", SPEC, 11, False)
    with caplog.at_level(logging.WARNING):
        shadowed = shadow_check.staged_run(tmp_path / "shadow", SPEC, 11, "shadow")
    assert [t["gate"] for t in shadowed["ticks"]] == [t["gate"] for t in off["ticks"]]
    assert not any(MARKER in r.getMessage() for r in caplog.records)
    return shadowed


@pytest.mark.parametrize("mode", ["raise", "timeout", "garbage-none", "garbage-text", "garbage-hold",
                                  "garbage-numbers"])
def test_a_failing_forecast(tmp_path, monkeypatch, caplog, mode):
    fired = []

    def broken(*a, **k):
        fired.append(1)
        if mode == "raise":
            raise ZeroDivisionError(MARKER)
        if mode == "timeout":
            raise TimeoutError(MARKER)
        return {"garbage-none": None, "garbage-text": MARKER, "garbage-hold": {"hold": "yes", "forecast": {}},
                "garbage-numbers": {"hold": True, "forecast": {"horizon_seconds": "x"}}}[mode]
    monkeypatch.setattr(forecast, "assess", broken)
    shadowed = both(tmp_path, caplog)
    assert fired
    assert any("shadow forecast" in r.getMessage() for r in caplog.records)
    out = shadow.report(shadowed["path"], shadowed["clock"])
    assert out["verdicts"]["failed"] > 0 and out["verdicts"]["holds"] == 0


def test_a_failing_history_read(tmp_path, monkeypatch, caplog):
    from neuroscifi import internal_state
    real, fired = internal_state.history, []

    def broken(db_path, limit=internal_state.HISTORY_KEPT, now=None):
        import inspect
        if any(f.function == "_verdict" for f in inspect.stack()[1:3]):
            fired.append(1)
            raise OSError(MARKER)
        return real(db_path, limit, now)
    monkeypatch.setattr(internal_state, "history", broken)
    shadowed = both(tmp_path, caplog)
    assert fired and any("OSError" in r.getMessage() for r in caplog.records)
    assert shadow.report(shadowed["path"], shadowed["clock"])["verdicts"]["failed"] > 0


def test_a_shadow_file_that_is_not_a_file(tmp_path, caplog):
    folder = tmp_path / "shadow" / "individual.sqlite.resources"
    folder.mkdir(parents=True)
    os.mkfifo(folder / shadow.FILE)
    shadowed = both(tmp_path, caplog)
    # a FIFO opened for writing without a reader: ENXIO, before the plain-file check could refuse it
    assert any("shadow" in r.getMessage() and ("NotAFile" in r.getMessage() or "ENXIO" in r.getMessage())
               for r in caplog.records)
    assert shadow.report(shadowed["path"], shadowed["clock"])["problems"]


def test_a_settlement_that_cannot_write(tmp_path, monkeypatch, caplog):
    """The sampler's settlement fails: the samples are still recorded, the gate is unchanged, the failure logged."""
    from neuroscifi.resources import shared
    real, fired = shared.write_text, []

    def broken(path, text, **kw):
        if str(path).endswith(shadow.DAYS_FILE):
            fired.append(1)
            raise OSError(28, MARKER)
        return real(path, text, **kw)
    monkeypatch.setattr(shared, "write_text", broken)
    shadowed = both(tmp_path, caplog)
    assert fired and any("settling the verdicts" in r.getMessage() and "ENOSPC" in r.getMessage()
                         for r in caplog.records)
    from neuroscifi import internal_state
    assert len(internal_state.history(shadowed["path"], now=shadowed["clock"])) == len(shadowed["ticks"])
    # nothing was lost: the report settles the verdicts the sampler could not
    out = shadow.report(shadowed["path"], shadowed["clock"])
    assert out["verdicts"]["verdicts"] + out["pending"] == len(shadowed["ticks"])
