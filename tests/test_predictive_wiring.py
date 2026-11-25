"""Wave 1 review B6: the cognitive benchmark's anticipation family hands lane I's forecaster the plant's history
directly, so the product's wiring is not exercised there. This drives that wiring end to end on a staged individual
(ADR-051): each tick of a benchmark trace is staged on the host's memory sample and the research queue, the worker's
sampler (internal_state.record, which the heartbeat calls) appends it to the history file, and the gate
(resources.policy.background_gate) reads the file and decides. Its holds must be the benchmark's on every tick."""
import time
from types import SimpleNamespace

from neuroscifi import internal_state
from neuroscifi.acceptance.cognition import anticipation, instinct, splits
from neuroscifi.instance import store
from neuroscifi.resources import monitor, policy
from neuroscifi.webcortex import orchestrator, state

T0 = 1_800_000_000.0


class Wired:
    """A regulator that decides through the product: stage the tick, record it, ask the gate."""

    def __init__(self, db, path, monkeypatch, tasks):
        self.db, self.path, self.tasks, self.mp = db, str(path), tasks, monkeypatch
        self.ticks = []

    def decide(self, history):
        o = history[-1]
        clock = T0 + o["seconds"]
        self.mp.setattr(monitor, "memory", lambda *a, **k: dict(o["memory"]))
        monitor.clear_cache()
        self.mp.setattr(policy, "time", SimpleNamespace(time=lambda: clock, monotonic=time.monotonic))
        with store.write(self.db):              # the plant's running background tasks, as the queue holds them
            for i, tid in enumerate(self.tasks):
                self.db.execute("UPDATE web_tasks SET status=? WHERE id=?",
                                ("reading" if i < o["background_running"] else "failed", tid))
        recorded = internal_state.record(self.path, now=clock)
        gate = policy.background_gate(self.path, audio=o["audio"])
        direct = instinct.PredictiveRegulator().decide(history)["background"] == "hold"
        self.ticks.append({"tick": o["tick"], "recorded": recorded is not None, "gate": gate,
                           "benchmark_hold": direct or anticipation._gate_refuses(o)})
        return {"background": "allow" if gate["allowed"] else "hold"}


def test_history_file_to_gate_decision_matches_the_benchmark_forecaster(tmp_path, monkeypatch):
    path = tmp_path / "individual.sqlite"
    db = store.create(path, T0)
    try:
        state.set_config(db, {"enabled": True}, T0)
        store.set_config(db, {"predictive_regulation": True}, T0)
        tasks = []
        for i in range(anticipation.CONCURRENT):
            prepared = orchestrator.prepare(db, f"What changed in staged package {i}?", T0, mode="fact_verification",
                                            origin="curiosity")
            with store.write(db):
                tasks.append(orchestrator.insert(db, prepared, T0))
        spec = next(t for t in splits.ANTICIPATION["development"]["traces"] if t["name"] == "fast memory leak")
        wired = Wired(db, path, monkeypatch, tasks)
        anticipation.simulate(spec, 11, wired)
    finally:
        db.close()
    ticks = wired.ticks
    assert len(ticks) == anticipation.TICKS and all(t["recorded"] for t in ticks)
    assert len(internal_state.history(str(path))) == anticipation.TICKS          # every tick reached the file
    mismatched = [t["tick"] for t in ticks if (not t["gate"]["allowed"]) != t["benchmark_hold"]]
    assert mismatched == []
    forecast_holds = [t["tick"] for t in ticks if t["gate"]["why"] == "forecast"]
    assert forecast_holds, "the forecast never held: the trace did not exercise the wiring"
    assert all("predictive regulation" in ticks[i]["gate"]["reason"] for i in forecast_holds)


def test_with_the_setting_off_the_same_history_never_holds_by_forecast(tmp_path, monkeypatch):
    """The wiring's switch: the same staged ticks with predictive_regulation off give no forecast hold."""
    path = tmp_path / "individual.sqlite"
    db = store.create(path, T0)
    try:
        state.set_config(db, {"enabled": True}, T0)
        spec = next(t for t in splits.ANTICIPATION["development"]["traces"] if t["name"] == "fast memory leak")
        wired = Wired(db, path, monkeypatch, [])
        anticipation.simulate(spec, 11, wired)
    finally:
        db.close()
    assert not [t for t in wired.ticks if t["gate"]["why"] == "forecast"]
    assert len(internal_state.history(str(path))) == anticipation.TICKS
