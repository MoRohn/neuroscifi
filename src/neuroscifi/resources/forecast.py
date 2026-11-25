"""Predictive regulation: a small, transparent forecast of memory headroom that can only make background work more
conservative (Project Cognitive Genesis, lane I; ADR-051).

What it does. The reactive gate (policy.background_gate) refuses new background research when memory pressure is
already "critical". A background task, once started, runs to its end (policy.py: "an autonomous task already running
continues"), so a task started just before a slow leak reaches the critical line still adds its memory on top of it.
assess() looks at the recent internal-state history (internal_state.history: one compact sample every
internal_state.RECORD_EVERY s, written by the research worker) and asks: if background work started now and ran at
the most the existing cap allows (policy.BACKGROUND_MODEL_SLOTS tasks), would available memory be under the critical
line (monitor.thresholds) within HORIZON_SECONDS, given the trend? If so, it says "hold"; policy.background_gate then
refuses new background starts with that reason, exactly as it does for critical pressure.

How (no learned black box; every number is in the result):
  footprint  memory one running background task uses: the median of -(change in available memory) / (change in the
             number of running background tasks) over the samples where that number changed; 0 until one was seen
  headroom   available memory as it would be with no background task running: available + footprint x running
  trend      Holt's linear exponential smoothing of headroom over time (level ALPHA, trend BETA), on the last WINDOW
             samples; a gap longer than MAX_GAP_SECONDS starts the series again
  forecast   level + trend x HORIZON_SECONDS; hold when forecast - footprint x slots < the critical line

What it never does: start, keep or protect anything. It adds holds on NEW background research only; owner work,
running tasks, idle unloading and every other gate are untouched, and it is off unless the owner turns it on
(InstanceConfig.predictive_regulation, `nsf config --predictive-regulation`). With too little history (fewer than
MIN_POINTS recent samples) it says nothing. HORIZON_SECONDS is declared (about one background research step), not
measured on the host."""
from __future__ import annotations

from statistics import median

from . import monitor

ALPHA = 0.5                  # level smoothing
BETA = 0.3                   # trend smoothing
HORIZON_SECONDS = 180.0      # how far ahead the forecast looks (declared: about one background research step)
WINDOW = 40                  # samples the trend is fitted on
MAX_POINTS = 720             # samples read at most (the voice pattern looks this far back)
MIN_POINTS = 4               # fewer recent samples than this: no forecast
MAX_GAP_SECONDS = 600.0      # a longer gap between samples starts the series again (the worker was not running)
STALE_SECONDS = 300.0        # a newest sample older than this: no forecast (the history is not current)
VOICE_STARTS = 2             # voice session starts needed before a pattern is read from them
VOICE_REGULARITY = 0.25      # ... whose intervals differ from their median by at most this share of it
VOICE_JITTER = 0.05          # timing allowance around an expected start: at least this share of the period
GIB = 1024 ** 3


def _number(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _points(history):
    """The usable samples, oldest first: those with a time, available and total memory, and a running count."""
    out = []
    for h in history or ():
        if not isinstance(h, dict):
            continue
        at, avail, total = _number(h.get("at")), _number(h.get("available_bytes")), _number(h.get("total_bytes"))
        running = _number(h.get("background_running"))
        if at is None or avail is None or not total:
            continue
        out.append({"at": float(at), "available": float(avail), "total": float(total),
                    "running": int(running or 0), "audio": h.get("audio") is True})
    out.sort(key=lambda p: p["at"])
    start = 0                                    # only the samples since the last long gap
    for i in range(1, len(out)):
        if out[i]["at"] - out[i - 1]["at"] > MAX_GAP_SECONDS:
            start = i
    return out[start:][-MAX_POINTS:]


def footprint(points) -> float:
    """Bytes of available memory one running background task takes (median over the samples where the number of
    running tasks changed; 0.0 before any such change was seen)."""
    ratios = []
    for a, b in zip(points, points[1:]):
        dn = b["running"] - a["running"]
        if dn:
            ratios.append(-(b["available"] - a["available"]) / dn)
    return max(0.0, median(ratios)) if ratios else 0.0


def holt(series):
    """(level, trend per second) of [(time, value)] by Holt's linear exponential smoothing."""
    (t0, level), trend = series[0], 0.0
    for t, value in series[1:]:
        dt = max(1e-9, t - t0)
        previous = level
        level = ALPHA * value + (1 - ALPHA) * (level + trend * dt)
        trend = BETA * (level - previous) / dt + (1 - BETA) * trend
        t0 = t
    return level, trend


def voice_pattern(points, now, horizon) -> dict | None:
    """The next voice session expected from a regular pattern of earlier ones, or None. Session starts are the
    samples where the audio flag turned on; with at least VOICE_STARTS of them whose intervals differ from their
    median by at most VOICE_REGULARITY of it, the next start is the last one plus that median. {"next_at",
    "period_seconds", "tolerance_seconds", "starts", "hold"}: hold while no session is on and the next start is
    within `horizon` (less the tolerance) or overdue by at most the tolerance."""
    starts = [b["at"] for a, b in zip(points, points[1:]) if b["audio"] and not a["audio"]]
    if len(starts) < VOICE_STARTS:
        return None
    gaps = [b - a for a, b in zip(starts, starts[1:])]
    period = median(gaps)
    spread = max(abs(g - period) for g in gaps)
    if period <= 0 or spread > VOICE_REGULARITY * period:
        return None
    step = median([b["at"] - a["at"] for a, b in zip(points, points[1:])] or [0.0])
    tolerance = max(spread, VOICE_JITTER * period) + step
    last = starts[-1]
    if now - last > 2 * period:               # the pattern stopped: no prediction from it
        return None
    upcoming = last + period
    while upcoming + tolerance < now:
        upcoming += period
    on = points[-1]["audio"]
    hold = not on and upcoming - tolerance - horizon <= now <= upcoming + tolerance
    return {"next_at": upcoming, "period_seconds": period, "tolerance_seconds": tolerance, "starts": len(starts),
            "hold": hold}


def assess(history, *, now=None, horizon=HORIZON_SECONDS, slots=None, use_trend=True, use_footprint=True,
           use_voice=True) -> dict:
    """{"hold": bool, "reason": str | None, "forecast": {...the numbers...} | None}: should new background work wait
    because memory headroom is forecast to fall under the critical line within `horizon` seconds, or because a voice
    session is expected within it? See the module doc. `slots` defaults to policy.BACKGROUND_MODEL_SLOTS;
    use_trend / use_footprint / use_voice switch those parts off (the benchmark's ablations)."""
    if slots is None:
        from .policy import BACKGROUND_MODEL_SLOTS as slots
    points = _points(history)
    if len(points) < MIN_POINTS:
        return {"hold": False, "reason": None, "forecast": None,
                "note": f"{len(points)} recent sample(s); a forecast needs {MIN_POINTS}"}
    newest = points[-1]
    now = newest["at"] if now is None else now
    if now - newest["at"] > STALE_SECONDS:
        return {"hold": False, "reason": None, "forecast": None,
                "note": f"the newest sample is {now - newest['at']:.0f} s old; no forecast from stale history"}
    voice = voice_pattern(points, now, horizon) if use_voice else None
    points = points[-WINDOW:]
    fp = footprint(points) if use_footprint else 0.0
    series = [(p["at"], p["available"] + fp * p["running"]) for p in points]
    level, trend = holt(series)
    if not use_trend:
        trend = 0.0
    projected = level + trend * horizon
    floor = monitor.thresholds(int(newest["total"]))["critical_below_bytes"]
    margin = projected - fp * slots - floor
    hold = margin < 0
    numbers = {"at": newest["at"], "horizon_seconds": horizon, "samples": len(points),
               "headroom_bytes": int(level), "trend_bytes_per_minute": round(trend * 60.0),
               "headroom_forecast_bytes": int(projected), "background_task_bytes": int(fp), "slots": slots,
               "critical_below_bytes": int(floor), "margin_bytes": int(margin)}
    reason = None
    if hold:
        reason = (f"forecast: memory headroom {projected / GIB:.1f} GiB in {horizon / 60:.0f} min "
                  f"(now {level / GIB:.1f} GiB, trend {trend * 60 / GIB:+.2f} GiB/min) less {slots} background "
                  f"task(s) of {fp / GIB:.1f} GiB would fall below the critical line {floor / GIB:.1f} GiB; new "
                  "background research waits (predictive regulation)")
    if voice is not None:
        numbers["voice"] = {k: round(v, 1) if isinstance(v, float) else v for k, v in voice.items()}
        if voice["hold"] and not hold:
            hold = True
            reason = (f"forecast: a voice session is expected in about {max(0.0, voice['next_at'] - now) / 60:.0f} "
                      f"min (the last {voice['starts']} started about every {voice['period_seconds'] / 60:.0f} "
                      "min); new background research waits so it does not run into it (predictive regulation)")
    return {"hold": hold, "reason": reason, "forecast": numbers}


__all__ = ["ALPHA", "BETA", "HORIZON_SECONDS", "MIN_POINTS", "WINDOW", "assess", "footprint", "holt"]
