"""Scales and tick choice for linear, log10 and time axes, and band scales for categories.

Linear ticks: the step is the smallest of {1, 2, 2.5, 5} x 10^k that gives at most `target` + 1 intervals over the
data; the domain is widened to the enclosing multiples of the step ("nice" domain), so the first and last ticks are
the domain's ends. Values are computed as integer multiples of the step and printed with the step's own number of
decimals (so 0.1 + 0.2 never prints as 0.30000000000000004).

Log10 ticks: powers of ten over the enclosing decades; when the data spans fewer than two decades, 2 and 5 times
each power are added; when it spans many, every k-th decade. Labels are plain decimals ("0.01", "1000").

Time ticks (values are seconds since 1970-01-01T00:00:00Z, UTC, no leap seconds): the smallest calendar step from
1 s to 1000 years whose count fits the target; days align to UTC midnight, weeks to Mondays, months and years to
the calendar. Labels are ISO 8601: "2026-03-01" for steps of a day or more, "2026-03-01T06:00Z" below a day,
"2026-03-01T06:00:30Z" below a minute. The time domain is the data's range (ticks fall inside it).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from .svg_base import Fit, fixed

EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
MAX_TICKS = 40


@dataclass(frozen=True)
class Ticks:
    values: tuple[float, ...]
    labels: tuple[str, ...]
    lo: float           # domain
    hi: float


@dataclass(frozen=True)
class Scale:
    """A continuous scale from a data domain to a pixel range (log10 when `log`)."""

    lo: float
    hi: float
    p0: float
    p1: float
    log: bool = False

    def __call__(self, value: float) -> float:
        if self.log:
            a, b, v = math.log10(self.lo), math.log10(self.hi), math.log10(value)
        else:
            a, b, v = self.lo, self.hi, value
        if b == a:
            return (self.p0 + self.p1) / 2
        return self.p0 + (v - a) / (b - a) * (self.p1 - self.p0)


def decimals_of(step: float) -> int:
    for d in range(0, 16):
        if abs(round(step, d) - step) <= abs(step) * 1e-9:
            return d
    return 15


def _pad(lo: float, hi: float) -> tuple[float, float]:
    if not (math.isfinite(lo) and math.isfinite(hi)):
        raise Fit("an axis has no finite values")
    if hi > lo:
        return lo, hi
    if lo == 0:
        return -1.0, 1.0
    d = abs(lo) * 0.1
    return lo - d, hi + d


def linear_ticks(lo: float, hi: float, target: int = 5, *, nice: bool = True) -> Ticks:
    """Nice ticks for [lo, hi]. With `nice` the domain is widened to the first and last tick."""
    lo, hi = _pad(lo, hi)
    target = max(2, min(int(target), MAX_TICKS))
    raw = (hi - lo) / target
    mag = 10.0 ** math.floor(math.log10(raw))
    step = None
    for m in (1, 2, 2.5, 5, 10, 20):
        cand = m * mag
        n = math.ceil(hi / cand - 1e-9) - math.floor(lo / cand + 1e-9)
        if n <= target:
            step = cand
            break
    if step is None:
        step = 20 * mag
    d = decimals_of(step)
    k0 = math.floor(lo / step + 1e-9)
    k1 = math.ceil(hi / step - 1e-9)
    if not nice:
        k0, k1 = math.ceil(lo / step - 1e-9), math.floor(hi / step + 1e-9)
    values = tuple(float(fixed(k * step, d)) for k in range(k0, k1 + 1))
    labels = tuple(fixed(v, d) for v in values)
    if nice:
        return Ticks(values, labels, values[0], values[-1])
    return Ticks(values, labels, lo, hi)


def _plain(value: float) -> str:
    if value >= 1:
        return fixed(value, 0)
    return fixed(value, max(0, -math.floor(math.log10(value) + 1e-9)))


def log_ticks(lo: float, hi: float, target: int = 5) -> Ticks:
    if lo <= 0 or hi <= 0:
        raise Fit("a log axis needs values above zero")
    lo, hi = _pad(lo, hi)
    e0 = math.floor(math.log10(lo) + 1e-12)
    e1 = math.ceil(math.log10(hi) - 1e-12)
    if e1 == e0:
        e1 += 1
    if e1 - e0 > 300:
        raise Fit("a log axis spans more than 300 decades")
    decades = e1 - e0
    values: list[float] = []
    if decades < 2:
        for e in range(e0, e1 + 1):
            for m in (1, 2, 5):
                v = float(f"{m}e{e}")
                if e == e1 and m > 1:
                    break
                values.append(v)
        values = [v for v in values if v >= float(f"1e{e0}")]
    else:
        every = max(1, math.ceil(decades / max(2, target)))
        e0 = (e0 // every) * every
        e1 = -((-e1) // every) * every
        values = [float(f"1e{e}") for e in range(e0, e1 + 1, every)]
    # Domain: the enclosing ticks.
    lo_t = max(v for v in values if v <= lo * (1 + 1e-12))
    hi_t = min(v for v in values if v >= hi * (1 - 1e-12))
    values = [v for v in values if lo_t <= v <= hi_t]
    return Ticks(tuple(values), tuple(_plain(v) for v in values), lo_t, hi_t)


# --- time -------------------------------------------------------------------------------------------------------

_SECOND_STEPS = (1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600, 7200, 3 * 3600, 6 * 3600, 12 * 3600,
                 86400, 2 * 86400)
_MONTH_STEPS = (1, 2, 3, 6)
_YEAR_STEPS = (1, 2, 5, 10, 20, 50, 100, 200, 500, 1000)
MIN_TIME = -62135596800.0     # 0001-01-01T00:00:00Z
MAX_TIME = 253402300799.0     # 9999-12-31T23:59:59Z


def to_datetime(seconds: float) -> datetime:
    return EPOCH + timedelta(seconds=seconds)


def to_seconds(dt: datetime) -> float:
    delta = dt - EPOCH
    return delta.days * 86400.0 + delta.seconds + delta.microseconds / 1e6


def iso(seconds: float, step: float) -> str:
    dt = to_datetime(seconds)
    if step >= 86400:
        return dt.strftime("%Y-%m-%d")
    if step >= 60:
        return dt.strftime("%Y-%m-%dT%H:%MZ")
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _second_ticks(lo, hi, step):
    if step == 7 * 86400:
        monday = 4 * 86400       # 1970-01-05 was a Monday
        k0 = math.ceil((lo - monday) / step)
        return [monday + k * step for k in range(k0, math.floor((hi - monday) / step) + 1)]
    return [k * step for k in range(math.ceil(lo / step), math.floor(hi / step) + 1)]


def _month_ticks(lo, hi, months):
    a, b = to_datetime(lo), to_datetime(hi)
    index = a.year * 12 + a.month - 1
    out = []
    while True:
        y, m = divmod(index, 12)
        if y > 9999:
            break
        t = to_seconds(datetime(y, m + 1, 1, tzinfo=timezone.utc))
        if t > hi:
            break
        if t >= lo and (m % months == 0):
            out.append(t)
        index += 1
        if len(out) > MAX_TICKS * 4:
            break
    del b
    return out


def _year_ticks(lo, hi, years):
    a, b = to_datetime(lo), to_datetime(hi)
    out = []
    y = (a.year // years) * years
    while y <= b.year and len(out) <= MAX_TICKS * 4:
        if 1 <= y <= 9999:
            t = to_seconds(datetime(y, 1, 1, tzinfo=timezone.utc))
            if lo <= t <= hi:
                out.append(t)
        y += years
    return out


def time_ticks(lo: float, hi: float, target: int = 5) -> Ticks:
    if lo < MIN_TIME or hi > MAX_TIME:
        raise Fit("a time lies outside the years 1 to 9999")
    if hi <= lo:
        lo, hi = lo - 43200, hi + 43200
    target = max(2, min(int(target), MAX_TICKS))
    candidates = ([("s", s) for s in _SECOND_STEPS] + [("s", 7 * 86400)] + [("m", m) for m in _MONTH_STEPS]
                  + [("y", y) for y in _YEAR_STEPS])
    best = None
    for unit, step in candidates:
        if unit == "s":
            span_ticks = (hi - lo) / step
            if span_ticks > target * 3:
                continue
            ticks = _second_ticks(lo, hi, step)
            approx = step
        elif unit == "m":
            if (hi - lo) / (step * 30 * 86400) > target * 3:
                continue
            ticks, approx = _month_ticks(lo, hi, step), 86400 * 28
        else:
            if (hi - lo) / (step * 365 * 86400) > target * 3:
                continue
            ticks, approx = _year_ticks(lo, hi, step), 86400 * 365
        if len(ticks) <= target + 1:
            if len(ticks) >= 2:
                best = (ticks, approx)
            break
        best = None
    if best is None:
        # Fewer than two calendar ticks fit: fall back to the smallest second step that gives at least two.
        for step in _SECOND_STEPS + (7 * 86400,):
            ticks = _second_ticks(lo, hi, step)
            if 2 <= len(ticks) <= target + 1:
                best = (ticks, step)
                break
    if best is None:
        raise Fit("no time ticks fit this range")
    ticks, approx = best
    return Ticks(tuple(float(t) for t in ticks), tuple(iso(t, approx) for t in ticks), lo, hi)


def ticks_for(kind: str, lo: float, hi: float, target: int, *, nice: bool = True) -> Ticks:
    if kind == "log":
        return log_ticks(lo, hi, target)
    if kind == "time":
        return time_ticks(lo, hi, target)
    return linear_ticks(lo, hi, target, nice=nice)


def band(n: int, p0: float, p1: float, padding: float = 0.2) -> tuple[list[float], float]:
    """Centres and band width of `n` equal categories across [p0, p1]."""
    width = (p1 - p0) / max(n, 1)
    return [p0 + width * (i + 0.5) for i in range(n)], width * (1 - padding)


__all__ = ["MAX_TIME", "MIN_TIME", "Scale", "Ticks", "band", "decimals_of", "iso", "linear_ticks", "log_ticks",
           "ticks_for", "time_ticks", "to_datetime", "to_seconds"]
