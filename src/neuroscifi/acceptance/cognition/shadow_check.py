"""The settlement check of predictive regulation in shadow (Wave 3 lane H; ADR-058), on the anticipation family's
traces (development and held-out; never the withheld family).

staged_run() drives the product's wiring as tests/test_predictive_wiring.py does: each tick of a trace is staged on
the host's memory sample and the research queue of a staged individual, the worker's sampler (internal_state.record,
which also settles the shadow verdicts every resources.shadow.SETTLE_EVERY s of the staged clock) appends it to the
history file, and the real gate (resources.policy.background_gate) decides; the plant follows the gate. With the
setting "shadow" the gate's decisions must be those with it off, tick by tick, and the plant's trace the same.

reference() is what the family's own scoring (scoring.breach_report) says of the forecast's verdicts on the trace
that happened (the plant under the gate, which shadow does not change), with that trace as its own reference: hold
ticks, false alarms, episodes, anticipated episodes, mean lead; and per verdict hits, misses and quiet allows. The
shadow report's settled numbers must equal it. Simulated plant: the constants are the benchmark's (anticipation.py),
not the host's; what this validates is the settlement's arithmetic and the wiring, not the forecast's value."""
from __future__ import annotations

from contextlib import ExitStack
from pathlib import Path
import time
from types import SimpleNamespace
from unittest import mock

from . import anticipation, instinct, scoring

DECISION = ("allowed", "reason", "why", "level", "audio")
T0 = 1_800_000_000.0


class _Wired:
    def __init__(self, db, path, tasks, stack):
        self.db, self.path, self.tasks, self.stack = db, str(path), tasks, stack
        self.ticks, self.observations, self.clock, self.gate_seconds = [], [], None, []

    def decide(self, history):
        from ... import internal_state
        from ...instance import store
        from ...resources import monitor, policy
        o = history[-1]
        clock = self.clock = T0 + o["seconds"]
        monitor.memory = lambda *a, **k: dict(o["memory"])
        monitor.clear_cache()
        policy.time = SimpleNamespace(time=lambda: clock, monotonic=time.monotonic)
        policy.audio_active = lambda *a, **k: bool(o["audio"])       # what the sampler records of the voice hub
        with store.write(self.db):
            for i, tid in enumerate(self.tasks):
                self.db.execute("UPDATE web_tasks SET status=? WHERE id=?",
                                ("reading" if i < o["background_running"] else "failed", tid))
        internal_state.record(self.path, now=clock)
        started = time.perf_counter()
        gate = policy.background_gate(self.path, audio=o["audio"])
        self.gate_seconds.append(time.perf_counter() - started)
        try:              # the forecast as the family calls it (None when it fails: the fault-injection tests)
            direct = instinct.PredictiveRegulator().decide(history)["background"] == "hold"
        except Exception:  # noqa: BLE001
            direct = None
        self.observations.append(o)
        self.ticks.append({"tick": o["tick"], "gate": {k: gate[k] for k in DECISION}, "forecast_hold": direct})
        return {"background": "allow" if gate["allowed"] else "hold"}


def staged_run(directory, spec, seed, setting, *, settle_every=None) -> dict:
    """One trace through the product's wiring in a fresh staged individual under `directory` (see the module doc).
    setting: the individual's predictive_regulation (False, True or "shadow"). settle_every replaces
    resources.shadow.SETTLE_EVERY for the run (None: as shipped)."""
    from ...instance import store
    from ...resources import monitor, policy, shadow
    from ...webcortex import orchestrator, state
    path = Path(directory) / "individual.sqlite"
    path.parent.mkdir(parents=True, exist_ok=True)
    with ExitStack() as stack:
        stack.enter_context(mock.patch.object(monitor, "memory", monitor.memory))
        stack.enter_context(mock.patch.object(policy, "time", policy.time))
        stack.enter_context(mock.patch.object(policy, "audio_active", policy.audio_active))
        stack.enter_context(mock.patch.object(shadow, "_seen", {}))
        stack.enter_context(mock.patch.object(shadow, "_settled_at", {}))
        if settle_every is not None:
            stack.enter_context(mock.patch.object(shadow, "SETTLE_EVERY", settle_every))
        db = store.create(path, T0)
        try:
            state.set_config(db, {"enabled": True}, T0)
            store.set_config(db, {"predictive_regulation": setting}, T0)
            tasks = []
            for i in range(anticipation.CONCURRENT):
                prepared = orchestrator.prepare(db, f"What changed in staged package {i}?", T0,
                                                mode="fact_verification", origin="curiosity")
                with store.write(db):
                    tasks.append(orchestrator.insert(db, prepared, T0))
            wired = _Wired(db, path, tasks, stack)
            anticipation.simulate(spec, seed, wired)
        finally:
            db.close()
            monitor.clear_cache()
    return {"path": str(path), "clock": wired.clock, "ticks": wired.ticks, "observations": wired.observations,
            "gate_seconds": wired.gate_seconds}


def reference(run: dict, *, floor_gib: float, horizon_ticks: int) -> dict:
    """The family's scoring of the forecast's verdicts on the trace that happened (see the module doc). Per-verdict
    counts cover the verdicts whose horizon ends within the trace (the others are still pending in the report);
    a verdict with fewer than forecast.MIN_POINTS samples behind it has no forecast and is not counted."""
    from ...resources import forecast
    flags = [o["available_gib"] < floor_gib for o in run["observations"]]
    holds = [t["forecast_hold"] for t in run["ticks"]]
    rep = scoring.breach_report(flags, flags, holds, horizon=horizon_ticks)
    end = len(holds) - horizon_ticks
    counted = range(forecast.MIN_POINTS - 1, end)
    ahead = [any(flags[t:t + horizon_ticks + 1]) for t in range(len(flags))]
    # review3 H-2: breach_report's own false alarms over the counted ticks (its holds outside them masked), compared
    # too; hits, misses and quiet have no breach_report counterpart and come from this check's own count
    inside = scoring.breach_report(flags, flags, [h and t in counted for t, h in enumerate(holds)],
                                   horizon=horizon_ticks)
    return {"hold_ticks_all": rep["hold_ticks"], "false_alarm_ticks_all": rep["false_alarm_ticks"],
            "false_alarm_ticks": inside["false_alarm_ticks"],
            "hits": sum(1 for t in counted if holds[t] and ahead[t]),
            "false_alarms": sum(1 for t in counted if holds[t] and not ahead[t]),
            "misses": sum(1 for t in counted if not holds[t] and ahead[t]),
            "quiet": sum(1 for t in counted if not holds[t] and not ahead[t]),
            "episodes": rep["reference_episodes"], "preceded": rep["anticipated"],
            "mean_lead_seconds": None if rep["mean_lead_ticks"] is None
            else rep["mean_lead_ticks"] * anticipation.TICK_SECONDS,
            "pending": min(horizon_ticks, len(holds))}


def settled(report: dict) -> dict:
    """The same quantities from a shadow report."""
    lead = report["breaches"]["lead_seconds"]
    return {"hits": report["settled"]["hits"], "false_alarms": report["settled"]["false_alarms"],
            "false_alarm_ticks": report["settled"]["false_alarms"],
            "misses": report["settled"]["misses"], "quiet": report["settled"]["quiet"],
            "episodes": report["breaches"]["episodes"], "preceded": report["breaches"]["preceded"],
            "mean_lead_seconds": None if lead is None else lead["mean"], "pending": report["pending"]}


def mismatches(report: dict, ref: dict) -> dict:
    """{quantity: (report's, reference's)} where they differ (the mean lead within 0.05 s: the report rounds to
    0.1 s)."""
    got, out = settled(report), {}
    for k, v in got.items():
        want = ref[k]
        if k == "mean_lead_seconds" and v is not None and want is not None:
            if abs(v - want) > 0.05:
                out[k] = (v, want)
        elif v != want:
            out[k] = (v, want)
    return out


__all__ = ["mismatches", "reference", "settled", "staged_run"]
