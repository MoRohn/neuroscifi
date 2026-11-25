"""Family 1, anticipation: does a regulator act on a coming threshold crossing before it happens, without crying wolf?

A trace is TICKS ticks of TICK_SECONDS of the host as a runtime sees it (splits.ANTICIPATION): foreground memory use,
voice sessions, model latency, queued owner tasks and the autonomous (background) work that wants to run. The future
of each trace is known, because the generator wrote it before anything ran.

The plant (bench-declared, not a measurement of the DGX Spark): background tasks run TASK_TICKS ticks once started
and cannot be preempted (resources/policy.py:5-8: "an autonomous task already running continues"); while background
work is allowed, up to CONCURRENT run at once, as many as the trace's demand. Each running task uses BG_TASK_GIB of
memory, adds BG_LATENCY_MS to model latency and occupies a worker. The existing gate is part of the plant and always
applies: no new background task starts while resources.policy.background_gate refuses (audio active, or memory
pressure "critical" by resources.monitor.pressure). A regulator can only ADD holds; it can never start work the gate
refuses (directive: act only inside existing bounds).

Breaches (per tick, per channel):
  memory   available memory below BREACH_FLOOR_GIB (close to out-of-memory; bench-declared: below the code's critical
           threshold, monitor.thresholds, resources/monitor.py:538-544)
  latency  model latency above LATENCY_LIMIT_MS (bench-declared: nothing in today's regulation reacts to latency;
           the gateway only enforces a whole-call deadline, inference/config.py:61 read_timeout 180 s)
  audio    a voice session is active while more than BACKGROUND_MODEL_SLOTS_AUDIO background tasks run
           (resources/policy.py:35: background model calls are capped at 1 while audio is active)
  backlog  an owner task is queued while every worker runs background work (WORKERS bench-declared, equal to the
           default concurrent autonomy cap, webcortex/autonomy.py:57)

The regulator interface: `decide(history) -> {"background": "allow" | "hold"}`; history is the list of observations
up to and including the current tick (never the future). The baseline regulator is today's reactive rule, run through
the real code: hold exactly when resources.policy.background_gate (resources/policy.py:134-145) refuses for the
current sample, whose level comes from resources.monitor.pressure (resources/monitor.py:551-573; thresholds
ELEVATED_GIB/CRITICAL_GIB and fractions at resources/monitor.py:49-52). Memory "elevated", latency and backlog
change nothing today.

Scores (scoring.breach_report): breaches avoided against the same trace with no regulation at all (no gate, no
holds), lead time of the first hold before each reference breach, false alarms (holds with no breach coming within
HORIZON ticks) and the background work given up. What it cannot show: that the real host behaves like this plant.
"""
from __future__ import annotations

import random

from ...resources import monitor, policy
from . import scoring

TICKS = 240
TICK_SECONDS = 30.0
TOTAL_GIB = 128.0
GIB = 1024 ** 3
BG_TASK_GIB = 12.0
BG_LATENCY_MS = 450.0
TASK_TICKS = 6
CONCURRENT = 2
WORKERS = 2
BREACH_FLOOR_GIB = 2.0
LATENCY_LIMIT_MS = 2500.0
HORIZON = 20
CHANNELS = ("memory", "latency", "audio", "backlog")


def _demand(spec, t) -> int:
    d = spec.get("demand", 2)
    if isinstance(d, (list, tuple)):
        value = 0
        for step in d:
            if t >= step["from"]:
                value = step["value"]
        return int(value)
    return int(d)


def exogenous(spec: dict, seed: int) -> list[dict]:
    """The trace without background work: per tick foreground memory use (GiB), audio, latency (ms), queued owner
    tasks and background demand. Deterministic for (spec, seed)."""
    rng = random.Random(f"anticipation:{spec['name']}:{seed}")
    mem, lat = spec.get("memory", {}), spec.get("latency", {})
    audio_spec, backlog = spec.get("audio"), spec.get("backlog")
    sessions = set()
    if audio_spec:
        k = 0
        while True:
            start = audio_spec["phase"] + k * audio_spec["period"]
            if start >= TICKS:
                break
            start += rng.randint(-audio_spec.get("jitter", 0), audio_spec.get("jitter", 0))
            sessions.update(range(max(0, start), min(TICKS, start + audio_spec["duration"])))
            k += 1
    out, leak = [], 0.0
    for t in range(TICKS):
        base, noise = mem.get("base_gib", 66.0), mem.get("noise_gib", 0.5)
        shift = mem.get("shift")
        if shift and t >= shift["at"]:
            base, noise = shift["to_gib"], shift.get("noise_gib", noise)
        for lk in mem.get("leaks", ()):
            if lk["start"] <= t < lk["until"]:
                leak += lk["slope"]
            elif t == lk["until"] and lk.get("release"):
                leak = 0.0
        step = sum(s["size"] for s in mem.get("steps", ()) if s["at"] <= t < s["at"] + s["for"])
        used = base + leak + step + rng.gauss(0.0, noise)
        latency = lat.get("base_ms", 900.0) + rng.gauss(0.0, lat.get("noise_ms", 60.0))
        for d in lat.get("drift", ()):
            if t >= d["start"]:
                latency += d["slope_ms"] * (min(t, d["until"]) - d["start"])
        latency += sum(s["size_ms"] for s in lat.get("steps", ()) if s["at"] <= t < s["at"] + s["for"])
        queued = 0
        if backlog and t >= backlog["phase"] and (t - backlog["phase"]) % backlog["period"] < backlog["duration"]:
            queued = backlog["size"]
        out.append({"used_gib": used, "audio": t in sessions, "latency_ms": max(50.0, latency), "queued": queued,
                    "demand": _demand(spec, t)})
    return out


def _observation(t, x, running) -> dict:
    available = max(0.0, TOTAL_GIB - x["used_gib"] - BG_TASK_GIB * running)
    mem = {"total_bytes": int(TOTAL_GIB * GIB), "available_bytes": int(available * GIB), "swap_total_bytes": 0,
           "swap_free_bytes": 0, "psi": None}
    level, reasons = monitor.pressure(mem)                          # the real thresholds
    return {"tick": t, "seconds": t * TICK_SECONDS, "memory": mem, "available_gib": available, "level": level,
            "reasons": reasons, "audio": x["audio"], "latency_ms": x["latency_ms"] + BG_LATENCY_MS * running,
            "backlog": x["queued"], "background_running": running, "demand": x["demand"]}


def _breaches(o) -> dict:
    return {"memory": o["available_gib"] < BREACH_FLOOR_GIB, "latency": o["latency_ms"] > LATENCY_LIMIT_MS,
            "audio": o["audio"] and o["background_running"] > policy.BACKGROUND_MODEL_SLOTS_AUDIO,
            "backlog": o["backlog"] > 0 and o["background_running"] >= WORKERS}


def _gate_refuses(o) -> bool:
    return not policy.background_gate(None, sample={"level": o["level"], "reasons": o["reasons"]},
                                      audio=o["audio"])["allowed"]


def simulate(spec: dict, seed: int, regulator=None, *, gate: bool = True) -> dict:
    """Run one trace. regulator None = no regulation (with gate=False: the reference run that defines which breaches
    are coming). Returns per-tick breaches, holds and background work."""
    xs = exogenous(spec, seed)
    tasks: list[int] = []                 # remaining ticks of each running background task
    history, holds, flags, work = [], [], {c: [] for c in CHANNELS}, 0
    for t, x in enumerate(xs):
        o = _observation(t, x, len(tasks))
        history.append(o)
        for c, v in _breaches(o).items():
            flags[c].append(v)
        work += len(tasks)
        action = regulator.decide(history) if regulator is not None else {"background": "allow"}
        hold = (action or {}).get("background") == "hold"
        holds.append(hold)
        refused = hold or (gate and _gate_refuses(o))
        tasks = [r - 1 for r in tasks if r > 1]
        if not refused:
            while len(tasks) < min(CONCURRENT, x["demand"]):
                tasks.append(TASK_TICKS)
    return {"flags": flags, "holds": holds, "work": work}


def score_trace(spec: dict, seed: int, regulator) -> dict:
    ref = simulate(spec, seed, None, gate=False)
    run = simulate(spec, seed, regulator)
    out = {c: scoring.breach_report(ref["flags"][c], run["flags"][c], run["holds"], horizon=HORIZON) for c in CHANNELS}
    out["background_work"] = run["work"]
    out["background_work_unregulated"] = ref["work"]
    out["work_given_up_share"] = 1 - run["work"] / ref["work"] if ref["work"] else 0.0
    # a hold is a false alarm only when no channel had a breach coming (the union of the reference channels)
    union = [any(ref["flags"][c][t] for c in CHANNELS) for t in range(TICKS)]
    out["false_alarm_ticks_any_channel"] = scoring.breach_report(union, union, run["holds"],
                                                                 horizon=HORIZON)["false_alarm_ticks"]
    return out


def _totals(rows) -> dict:
    """Summed over the traces of one seed."""
    out = {}
    for c in CHANNELS:
        ref = sum(r[c]["reference_episodes"] for r in rows)
        avoided = sum(r[c]["avoided"] for r in rows)
        leads = [r[c]["mean_lead_ticks"] for r in rows if r[c]["mean_lead_ticks"] is not None]
        out[c] = {"reference_episodes": ref, "episodes": sum(r[c]["episodes"] for r in rows),
                  "breach_ticks": sum(r[c]["ticks"] for r in rows), "avoided": avoided,
                  "avoided_share": avoided / ref if ref else None,
                  "anticipated": sum(r[c]["anticipated"] for r in rows),
                  "mean_lead_ticks": sum(leads) / len(leads) if leads else None}
    hold = sum(r["memory"]["hold_ticks"] for r in rows)
    out["hold_ticks"] = hold
    out["false_alarm_ticks"] = sum(r["false_alarm_ticks_any_channel"] for r in rows)
    out["false_alarm_share"] = out["false_alarm_ticks"] / hold if hold else None
    out["work_given_up_share"] = 1 - sum(r["background_work"] for r in rows) / max(
        1, sum(r["background_work_unregulated"] for r in rows))
    out["breach_ticks_all_channels"] = sum(out[c]["breach_ticks"] for c in CHANNELS)
    return out


def evaluate(traces, seeds, make_regulator) -> dict:
    """Every trace under every seed with a fresh regulator; per-trace means and totals with their spread over
    seeds."""
    per_seed, per_trace = [], {}
    for seed in seeds:
        rows = []
        for spec in traces:
            row = score_trace(spec, seed, make_regulator())
            rows.append(row)
            per_trace.setdefault(spec["name"], []).append(row)
        per_seed.append(_totals(rows))
    keys = [(c, k) for c in CHANNELS for k in ("reference_episodes", "breach_ticks", "avoided_share",
                                                  "mean_lead_ticks")]
    summary = {f"{c}.{k}": scoring.spread([s[c][k] for s in per_seed]) for c, k in keys}
    for k in ("hold_ticks", "false_alarm_share", "work_given_up_share", "breach_ticks_all_channels"):
        summary[k] = scoring.spread([s[k] for s in per_seed])
    traces_out = {}
    for name, rows in per_trace.items():
        traces_out[name] = {c: {k: scoring.spread([r[c][k] for r in rows])["mean"]
                                                for k in ("reference_episodes", "ticks", "avoided", "mean_lead_ticks")}
                            for c in CHANNELS}
        for k in ("work_given_up_share", "false_alarm_ticks_any_channel"):
            traces_out[name][k] = scoring.spread([r[k] for r in rows])["mean"]
        traces_out[name]["hold_ticks"] = scoring.spread([r["memory"]["hold_ticks"] for r in rows])["mean"]
    return {"seeds": list(seeds), "scope": SCOPE, "summary": summary, "per_seed": per_seed, "traces": traces_out}


# What a result of this family is and is not (Wave 1 review B6), carried in every output.
SCOPE = {
    "plant": "simulated: the constants (CONSTANTS: task memory, task length, breach lines) are declared by the "
             "benchmark, not measured on the host",
    "wiring": "the regulator is called directly with the plant's history; the product's wiring (the gate "
              "resources.policy.background_gate, the history file <db>.resources/internal-state.jsonl and the "
              "worker's sampler internal_state.record) is not exercised by this family; "
              "tests/test_predictive_wiring.py drives it end to end on a staged individual",
    "reading": "a gain here is a gain inside this plant; for lane I's forecaster on held-out it comes from reserving "
               "memory per background task (removing the trend leaves all of it there; on development the trend "
               "matters more: scripts/bench_wave1.py ablations, trend_share_of_avoided)"}


class ReactiveRegulator:
    """Today's rule: hold background work exactly when the existing gate refuses (resources/policy.py:134-145)."""

    def decide(self, history):
        return {"background": "hold" if _gate_refuses(history[-1]) else "allow"}


class OpenRegulator:
    """Reference only: never adds a hold (the plant's gate still applies). Equal to the baseline by construction,
    since the baseline adds nothing the gate does not already do; kept so a test can prove that."""

    def decide(self, history):
        return {"background": "allow"}


def run(split_spec: dict, make_regulator, *, seeds=None) -> dict:
    return evaluate(split_spec["traces"], seeds or split_spec["seeds"], make_regulator)


CONSTANTS = {"ticks": TICKS, "tick_seconds": TICK_SECONDS, "total_gib": TOTAL_GIB, "bg_task_gib": BG_TASK_GIB,
             "bg_latency_ms": BG_LATENCY_MS, "task_ticks": TASK_TICKS, "concurrent": CONCURRENT, "workers": WORKERS,
             "breach_floor_gib": BREACH_FLOOR_GIB, "latency_limit_ms": LATENCY_LIMIT_MS, "horizon_ticks": HORIZON,
             "code_thresholds_at_128_gib": monitor.thresholds(int(TOTAL_GIB * GIB)),
             "background_model_slots": policy.BACKGROUND_MODEL_SLOTS,
             "background_model_slots_audio": policy.BACKGROUND_MODEL_SLOTS_AUDIO}
