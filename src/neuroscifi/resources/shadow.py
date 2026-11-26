"""Predictive regulation in shadow: the forecast's verdict recorded where the gate would consult it, then settled
against what the host's memory did (Project Cognitive Genesis, Wave 3 lane H; ADR-058).

Why. ADR-051's forecast (resources/forecast.py) avoided most memory breaches on a SIMULATED plant with declared
constants; whether to let it hold background work on the real host is the owner's decision, and there was no host
evidence. With `nsf config --predictive-regulation-shadow` (InstanceConfig.predictive_regulation = "shadow") the
gate behaves exactly as with the setting off, and this module keeps the evidence.

  observe(db_path, now, gate_why)   called by policy.background_gate in shadow mode, after its own decision is made
                                    and whatever it is: the forecast for the newest internal-state sample, at most
                                    once per sample in this process, appended to <db>.resources/forecast-shadow.jsonl
                                    as numbers and fixed words only. It never raises and its result is not read by
                                    the gate: a forecast or a file that fails is logged (its kind, never a message)
                                    and, when the file can be written, recorded as {"failed": kind}
  settle_due(db_path, now)          called by the research worker's sampler (internal_state.record, every 30 s), at
                                    most every SETTLE_EVERY s: each verdict whose horizon has passed is settled
                                    against the history samples (internal-state.jsonl) and added to a daily roll-up,
                                    <db>.resources/forecast-shadow-days.json (KEPT_DAYS days); the verdict file keeps
                                    only what is not settled yet. No new background activity: it runs inside the
                                    sampler, and only while there is a verdict file
  report(db_path, now)              `nsf internal-state --shadow-report`: the roll-up plus the verdicts not settled
                                    yet whose horizon has passed (settled in memory, nothing written)

Settlement (one definition, checked against scoring.breach_report on the anticipation family's traces):
  breach        a history sample whose available memory is under the code's critical line (monitor.thresholds of
                its total), the line the forecast predicts a crossing of
  hit           a would-hold verdict at t with a breach sample in [t, t + horizon]
  false alarm   a would-hold verdict at t with none (a hold for an expected voice session is counted against memory
                too; voice_followed says how many of those a voice session did follow)
  miss          a would-allow verdict at t with a breach sample in [t, t + horizon]
  episode       a run of breach samples (a gap over forecast.MAX_GAP_SECONDS starts a new one); it was preceded when
                a would-hold verdict at t had t <= start <= t + horizon, by start - the earliest such t
  work          background starts (net increases of running background tasks from one sample to the next) after a
                would-hold verdict the real gate allowed: what holding would have deferred. A proxy: a task that ended
                while another started between two samples is not seen
What the forecast ADDS (review3 H-1): a hit counts a would-hold made while memory is already under the line, where
the real gate already refuses. `added` counts only the verdicts at which the real gate allowed and memory was not
under the line at the verdict's own sample: the holds that would have changed something (added hits, added false
alarms), and the allows that let a breach come (added misses). `preceded_before_start` counts episodes with a
would-hold strictly before their first breach sample (`preceded` includes one AT it, lead 0). The lead cannot exceed
the verdict's horizon, so it is reported as a distribution (0 s, up to 60, 120, 180 s, beyond), never only as a mean.
`holds_gate_refused`: would-holds made where the real gate already refused (audio or memory).
A limit no counting removes: verdicts exist only when the worker asks the gate (it claims or checks caps), while
breach episodes are counted from every history sample, also when research is disabled or writes are paused.
A verdict with no forecast (too little or stale history) is counted apart; one whose horizon holds no sample (the
worker stopped) is "unsettled". The history keeps 6 hours: a verdict not settled within that is unsettled too.

The files hold numbers and fixed words only: no text, no subject, no identifier. Like the internal-state history
(ADR-051) they are operational telemetry of the host: not in checkpoints, and not anything `forget` must reach;
deleting them loses only the evidence. Only the service account writes them (0640 in a folder two accounts share,
resources/shared.py); a file another account created is not read, every line is parsed bounded (shared.loads) and
checked, and the verdict file takes no line beyond MAX_BYTES. Nothing here is evidence or knowledge of the
individual, and nothing reads it but the report."""
from __future__ import annotations

import bisect
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import json
import logging
import math
import os
import re
import time

from . import shared

log = logging.getLogger(__name__)

SCHEMA = "nsf.forecast_shadow/1"
FILE = "forecast-shadow.jsonl"
DAYS_FILE = "forecast-shadow-days.json"
LOCK_FILE = "forecast-shadow.lock"
MAX_BYTES = 1 << 20           # the verdict file takes no line beyond this (about 5,000 verdicts: 40 h at one per 30 s)
SETTLE_EVERY = 300.0          # seconds between two settlements by the sampler of one process
KEPT_DAYS = 120               # days the roll-up keeps (the oldest goes first)
MAX_HORIZON = 3600.0          # a verdict whose horizon is not in (0, this] is ignored
FUTURE_SECONDS = 60.0         # a verdict dated later than now + this is ignored
MIN_DAYS = 7                  # fewer days with verdicts than this: "not enough data"
MIN_EPISODES = 5              # ... or fewer breach episodes than this
GATES = ("allowed", "audio", "memory")
WHYS = ("memory", "voice")
VERDICT_KEYS = ("verdicts", "holds", "allows", "no_forecast", "failed", "hits", "false_alarms", "misses", "quiet",
                "unsettled", "memory_holds", "memory_hits", "voice_holds", "voice_followed", "holds_gate_allowed",
                "starts_under_hold",
                # review3 H-1: what the forecast adds where the real gate allowed (see the module doc)
                "added_hits", "added_false_alarms", "added_misses", "added_quiet", "holds_gate_refused")
POINT_KEYS = ("samples", "breach_samples", "starts")
LEAD_BUCKETS = (("0", 0.0), ("1-60", 60.0), ("61-120", 120.0), ("121-180", 180.0), ("over 180", math.inf))
EPISODE_KEYS = ("episodes", "preceded", "preceded_before_start") + tuple(f"lead {name}" for name, _ in LEAD_BUCKETS)
LEAD_KEYS = ("lead_seconds_sum", "lead_seconds_min", "lead_seconds_max")
DAY = re.compile(r"\d{4}-\d{2}-\d{2}")
DAY_SECONDS = 86_400.0

_seen: dict = {}              # db_path -> the history file's state when its verdict was taken (this process)
_settled_at: dict = {}        # db_path -> when this process's sampler last settled
_failures: dict = {}          # kind -> count, this process (logged at 1, 10, 100, ...)


def _folder(db_path):
    from .. import internal_state
    return internal_state.folder(db_path)


def _number(value):
    """A finite int or float, else None (the files are in a folder another account can write)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        return value if math.isfinite(value) else None
    except OverflowError:
        return None


def _day(at) -> str:
    return datetime.fromtimestamp(at, timezone.utc).strftime("%Y-%m-%d")


def _note_failure(where, exc) -> str:
    from ..instance.errors import failure_kind
    kind = f"{where}: {failure_kind(exc)}"
    if kind not in _failures and len(_failures) > 64:
        _failures.clear()
    count = _failures[kind] = _failures.get(kind, 0) + 1
    if count == 1 or count == 10 ** (len(str(count)) - 1):
        log.warning("predictive regulation in shadow: %s failed (%d in this process); the gate is unchanged",
                    kind, count)
    return failure_kind(exc)


@contextmanager
def _locked(where, *, wait):
    """The shadow files' lock (an exclusive flock on forecast-shadow.lock); yields False when it is held and
    wait is False (the gate never waits)."""
    fd = shared.open_file(where / LOCK_FILE, os.O_RDWR)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | (0 if wait else fcntl.LOCK_NB))
        except BlockingIOError:
            yield False
            return
        yield True
    finally:
        os.close(fd)


def _history_state(where):
    from .. import internal_state
    try:
        st = os.stat(where / internal_state.HISTORY_FILE)
    except OSError:
        return None
    return (st.st_ino, st.st_mtime_ns, st.st_size)


# ---- recording (the gate's path) -------------------------------------------------------------------------------------

def observe(db_path, now, gate_why) -> dict | None:
    """Record the forecast's verdict for the newest history sample (see the module doc). Returns what was recorded,
    or None (nothing new to record, or a failure, which is logged). Never raises."""
    try:
        return _observe(db_path, now, gate_why)
    except Exception as exc:   # noqa: BLE001 - the gate's decision is made already; a failure here changes nothing
        _note_failure("recording the verdict", exc)
        return None


def _observe(db_path, now, gate_why):
    where = _folder(db_path)
    if where is None:
        return None
    state, key = _history_state(where), str(db_path)
    if key in _seen and _seen[key] == state:
        return None                                   # this sample's verdict is recorded already
    item = {"v": 1, "at": round(float(now), 3), "gate": gate_why if gate_why in GATES[1:] else "allowed"}
    try:
        item |= _verdict(db_path, now)
    except Exception as exc:   # noqa: BLE001 - a forecast that fails is visible in the record and the log
        item["failed"] = _note_failure("the shadow forecast", exc)
    shared.ensure_folder(where)
    with _locked(where, wait=False) as got:
        if not got:
            return None                               # being settled now: the next gate call records it
        fd = shared.open_file(where / FILE, os.O_WRONLY | os.O_APPEND, group_write=False)
        try:
            if os.fstat(fd).st_uid not in shared.writers(db_path):    # security review L5: as every read checks
                raise shared.NotOwned("the shadow record belongs to another account; nothing is appended to it")
            if os.fstat(fd).st_size >= MAX_BYTES:
                raise OSError("the shadow record is full; the sampler has not settled it")
            os.write(fd, (json.dumps(item, sort_keys=True) + "\n").encode("ascii"))
        finally:
            os.close(fd)
    if len(_seen) > 64:
        _seen.clear()
    _seen[key] = state
    return item


def _verdict(db_path, now) -> dict:
    """The forecast where the gate consults it (the same history read and the same call as with the setting on)."""
    from .. import internal_state
    from . import forecast
    samples = internal_state.history(db_path, now=now)
    if not isinstance(samples, list):
        raise TypeError(f"the history is {type(samples).__name__}, not a list of samples")
    verdict = forecast.assess(samples, now=now)
    if not isinstance(verdict, dict):
        raise TypeError(f"the forecast is {type(verdict).__name__}, not a verdict")
    numbers = verdict.get("forecast")
    if numbers is None:
        return {"hold": None, "sample_at": None}      # too little or stale history: no forecast
    if not isinstance(numbers, dict):
        raise TypeError(f"the forecast's numbers are {type(numbers).__name__}")
    horizon, margin, at = (_number(numbers.get(k)) for k in ("horizon_seconds", "margin_bytes", "at"))
    if horizon is None or margin is None or at is None or not isinstance(verdict.get("hold"), bool):
        raise ValueError("the forecast's verdict is incomplete")
    hold = verdict["hold"]
    return {"hold": hold, "why": ("memory" if margin < 0 else "voice") if hold else None,
            "horizon_seconds": float(horizon), "margin_bytes": int(margin), "sample_at": at}


# ---- reading the files -----------------------------------------------------------------------------------------------

def _read_verdicts(where, db_path, now) -> tuple[list[dict], int, str | None]:
    """(checked verdict lines oldest first, lines ignored, a problem or None)."""
    try:
        text = shared.read_text(where / FILE, limit=MAX_BYTES + (64 << 10), tail=True, owners=shared.writers(db_path))
    except FileNotFoundError:
        return [], 0, None
    except shared.NotOwned:
        return [], 0, "the shadow record belongs to another account; it is not read"
    except (OSError, ValueError):
        return [], 0, "the shadow record could not be read"
    out, ignored = [], 0
    for line in text.splitlines():
        try:
            item = _checked(shared.loads(line), now)
        except ValueError:
            ignored += 1
            continue
        out.append(item)
    out.sort(key=lambda v: v["at"])
    return out, ignored, None


def _checked(raw, now) -> dict:
    """One verdict line as written by _observe, or ValueError."""
    if not isinstance(raw, dict):
        raise ValueError("not a verdict")
    at = _number(raw.get("at"))
    if at is None or at < 0 or at > now + FUTURE_SECONDS:
        raise ValueError("no time")
    gate = raw.get("gate")
    if gate not in GATES:
        raise ValueError("no gate")
    if "failed" in raw:
        return {"at": float(at), "failed": True, "gate": gate}
    hold = raw.get("hold")
    if hold is None:
        return {"at": float(at), "hold": None, "gate": gate}
    if not isinstance(hold, bool):
        raise ValueError("no verdict")
    horizon, sample_at = _number(raw.get("horizon_seconds")), _number(raw.get("sample_at"))
    if horizon is None or not 0 < horizon <= MAX_HORIZON or sample_at is None:
        raise ValueError("no horizon")
    why = raw.get("why")
    if hold and why not in WHYS:
        raise ValueError("no reason")
    return {"at": float(at), "hold": hold, "why": why if hold else None, "horizon": float(horizon),
            "sample_at": float(sample_at), "gate": gate}


def _points(samples) -> list[dict]:
    """The history samples settlement uses, oldest first: a time, available and total memory, the running count
    and the audio flag (a sample that cannot be true is left out, as resources.forecast does)."""
    out = []
    for h in samples or ():
        if not isinstance(h, dict):
            continue
        at, avail, total = _number(h.get("at")), _number(h.get("available_bytes")), _number(h.get("total_bytes"))
        running = _number(h.get("background_running"))
        if at is None or avail is None or not total or total <= 0 or not 0 <= avail <= total or total > 1 << 60:
            continue
        if running is not None and not 0 <= running <= 1024:
            continue
        out.append({"at": float(at), "available": float(avail), "total": float(total), "running": int(running or 0),
                    "audio": h.get("audio") is True})
    out.sort(key=lambda p: p["at"])
    return out


def _empty_day() -> dict:
    return {k: 0 for k in VERDICT_KEYS + POINT_KEYS + EPISODE_KEYS} | {"lead_seconds_sum": 0.0,
                                                                        "lead_seconds_min": None,
                                                                        "lead_seconds_max": None}


def _read_days(where, db_path, now) -> tuple[dict, str | None]:
    """The roll-up ({"settled_until", "samples_until", "days"}), checked; a missing file is an empty one. A problem
    (another account's file, one that cannot be read) is returned with an empty roll-up. A day later than tomorrow
    (by `now`) and a lead time beyond MAX_HORIZON are not taken (security review L5: forged future days would push
    the real ones out of the KEPT_DAYS kept, and a lead of 1e300 would be the report's mean)."""
    latest = _day(now + DAY_SECONDS)
    empty = {"settled_until": None, "samples_until": None, "days": {}}
    try:
        raw = shared.read_json(where / DAYS_FILE, limit=1 << 20, owners=shared.writers(db_path))
    except FileNotFoundError:
        return empty, None
    except shared.NotOwned:
        return empty, "the shadow roll-up belongs to another account; it is not read"
    except (OSError, ValueError):
        return empty, "the shadow roll-up could not be read"
    if not isinstance(raw, dict) or not isinstance(raw.get("days"), dict) or len(raw["days"]) > 2 * KEPT_DAYS:
        return empty, "the shadow roll-up is not one this version writes"
    out = {"settled_until": _number(raw.get("settled_until")), "samples_until": _number(raw.get("samples_until")),
           "days": {}}
    for day, counts in raw["days"].items():
        if not isinstance(day, str) or not DAY.fullmatch(day) or day > latest or not isinstance(counts, dict):
            continue
        row = _empty_day()
        for k in VERDICT_KEYS + POINT_KEYS + EPISODE_KEYS:
            v = counts.get(k)
            row[k] = v if isinstance(v, int) and not isinstance(v, bool) and v >= 0 else 0
        for k in LEAD_KEYS:
            v = _number(counts.get(k))
            bound = MAX_HORIZON * row["preceded"] if k == "lead_seconds_sum" else MAX_HORIZON
            if v is not None and 0 <= v <= bound:
                row[k] = float(v)
        out["days"][day] = row
    return out, None


# ---- settlement ------------------------------------------------------------------------------------------------------

def _settle(verdicts, points, *, since, until, samples_until, window=None, floor_bytes=None):
    """Settle what became due in (since, until] (see the module doc). Returns (counts per day, the verdicts still
    pending, the newest sample time settled). window / floor_bytes replace each verdict's horizon / the critical
    line (the benchmark's validation only)."""
    from . import forecast, monitor
    since = -math.inf if since is None else since
    samples_until = -math.inf if samples_until is None else samples_until
    days: dict = {}

    def bump(at, key, n=1):
        row = days.setdefault(_day(at), _empty_day())
        row[key] += n

    lines = {}
    for p in points:
        total = int(p["total"])
        if total not in lines:
            lines[total] = floor_bytes if floor_bytes is not None else monitor.thresholds(total)["critical_below_bytes"]
        p["breach"] = p["available"] < lines[total]
    times = [p["at"] for p in points]

    def within(t, h):
        return points[bisect.bisect_left(times, t):bisect.bisect_right(times, t + h)]

    # one verdict per sample: the first recorded (two processes, or a restart, may record the same sample)
    first, kept = set(), []
    for v in verdicts:
        sample = v.get("sample_at")
        if sample is not None:
            if sample in first or sample <= samples_until:
                continue
            first.add(sample)
        kept.append(v)
    horizon = (lambda v: window) if window is not None else (lambda v: v.get("horizon", 0.0))
    holds = [v for v in kept if v.get("hold") is True]
    pending, newest = [], samples_until
    for v in kept:
        t = v["at"]
        if v.get("hold") is None:        # no forecast, or a failure: nothing to wait for; settled once a sample is
            old, open_ = t < since, t >= until        # newer (a verdict may share its sample's time)
        else:
            old, open_ = t + horizon(v) <= since, t + horizon(v) > until
        if old:
            continue                                  # settled by an earlier pass (a compaction that did not finish)
        if open_:
            pending.append(v)
            continue
        bump(t, "verdicts")
        if v.get("failed"):
            bump(t, "failed")
            continue
        if v.get("hold") is None:
            bump(t, "no_forecast")
            continue
        newest = max(newest, v["sample_at"])
        bump(t, "holds" if v["hold"] else "allows")
        seen = within(t, horizon(v))
        if not seen:
            bump(t, "unsettled")
            continue
        breach = any(p["breach"] for p in seen)
        i = bisect.bisect_left(times, v["sample_at"])
        own_breach = i < len(points) and points[i]["at"] == v["sample_at"] and points[i]["breach"]
        if v["gate"] == "allowed" and not own_breach:          # where holding would have changed something
            bump(t, ("added_hits" if breach else "added_false_alarms") if v["hold"] else
                 ("added_misses" if breach else "added_quiet"))
        if v["hold"] and v["gate"] != "allowed":
            bump(t, "holds_gate_refused")
        if v["hold"]:
            bump(t, "hits" if breach else "false_alarms")
            if v["why"] == "memory":
                bump(t, "memory_holds")
                if breach:
                    bump(t, "memory_hits")
            else:
                bump(t, "voice_holds")
                if any(p["audio"] for p in seen):
                    bump(t, "voice_followed")
            if v["gate"] == "allowed":
                bump(t, "holds_gate_allowed")
                i = bisect.bisect_left(times, v["sample_at"])
                if i + 1 < len(points) and points[i]["at"] == v["sample_at"]:
                    bump(t, "starts_under_hold", max(0, points[i + 1]["running"] - points[i]["running"]))
        else:
            bump(t, "misses" if breach else "quiet")
    previous = None
    for p in points:
        start = p["breach"] and (previous is None or not previous["breach"]
                                 or p["at"] - previous["at"] > forecast.MAX_GAP_SECONDS)
        if since < p["at"] <= until:
            bump(p["at"], "samples")
            if p["breach"]:
                bump(p["at"], "breach_samples")
            if previous is not None and p["at"] - previous["at"] <= forecast.MAX_GAP_SECONDS:
                bump(p["at"], "starts", max(0, p["running"] - previous["running"]))
            if start:
                s = p["at"]
                bump(s, "episodes")
                early = [v["at"] for v in holds if v["at"] <= s <= v["at"] + horizon(v)]
                if early:
                    lead = s - min(early)
                    row = days[_day(s)]
                    row["preceded"] += 1
                    row["preceded_before_start"] += lead > 0
                    row["lead " + next(name for name, top in LEAD_BUCKETS if lead <= top)] += 1
                    row["lead_seconds_sum"] += lead
                    row["lead_seconds_min"] = lead if row["lead_seconds_min"] is None else min(row["lead_seconds_min"],
                                                                                                 lead)
                    row["lead_seconds_max"] = lead if row["lead_seconds_max"] is None else max(row["lead_seconds_max"],
                                                                                                 lead)
        previous = p
    return days, pending, newest


def _merge(into: dict, days: dict) -> None:
    for day, row in days.items():
        old = into.setdefault(day, _empty_day())
        for k in VERDICT_KEYS + POINT_KEYS + EPISODE_KEYS:
            old[k] += row[k]
        old["lead_seconds_sum"] += row["lead_seconds_sum"]
        for k, pick in (("lead_seconds_min", min), ("lead_seconds_max", max)):
            if row[k] is not None:
                old[k] = row[k] if old[k] is None else pick(old[k], row[k])


def settle_due(db_path, now=None) -> dict | None:
    """The sampler's settlement (see the module doc): at most every SETTLE_EVERY s per process, only when a verdict
    file exists. Returns {"settled_until", "pending"} or None when nothing was done. Raises on a file it cannot
    write (internal_state.record's caller logs it)."""
    where = _folder(db_path)
    if where is None or not os.path.exists(where / FILE):
        return None
    now = time.time() if now is None else now
    key = str(db_path)
    if now - _settled_at.get(key, -math.inf) < SETTLE_EVERY:
        return None
    if len(_settled_at) > 64:
        _settled_at.clear()
    _settled_at[key] = now
    from .. import internal_state
    # security review L4: never wait (this runs on the worker's heartbeat thread, and any account of the folder's
    # group can hold the lock): a held lock skips this pass, logged by settle_quietly; the next pass settles it
    with _locked(where, wait=False) as got:
        if not got:
            raise BlockingIOError("the shadow files are locked by another process; this settlement is skipped")
        rollup, problem = _read_days(where, db_path, now)
        if problem is not None:
            raise OSError(problem)
        verdicts, _, problem = _read_verdicts(where, db_path, now)
        if problem is not None:
            raise OSError(problem)
        points = _points(internal_state.history(db_path, limit=0, now=now))
        if not points:
            return None
        until = points[-1]["at"]
        if rollup["settled_until"] is not None and until <= rollup["settled_until"]:
            return None
        days, pending, newest = _settle(verdicts, points, since=rollup["settled_until"], until=until,
                                        samples_until=rollup["samples_until"])
        _merge(rollup["days"], days)
        kept = dict(sorted(rollup["days"].items())[-KEPT_DAYS:])
        doc = {"schema": SCHEMA, "v": 1, "settled_until": until,
               "samples_until": None if newest == -math.inf else newest, "days": kept}
        shared.write_text(where / DAYS_FILE, json.dumps(doc, sort_keys=True), group_write=False)
        # then the verdict file keeps what is not settled (a crash in between: the next pass skips what the
        # roll-up's settled_until already covers)
        shared.write_text(where / FILE, "".join(json.dumps(_line(v), sort_keys=True) + "\n" for v in pending),
                          group_write=False)
    return {"settled_until": until, "pending": len(pending)}


def settle_quietly(db_path, now=None) -> None:
    """settle_due(), never raising (the sampler's call): a failure is logged by its kind, at 1, 10, 100, ..."""
    try:
        settle_due(db_path, now)
    except Exception as exc:   # noqa: BLE001 - the sample is recorded; the settlement waits for the next pass
        _note_failure("settling the verdicts", exc)


def _line(v) -> dict:
    """A checked verdict as the line _observe writes."""
    if v.get("failed"):
        return {"v": 1, "at": v["at"], "gate": v["gate"], "failed": "earlier"}
    if v.get("hold") is None:
        return {"v": 1, "at": v["at"], "gate": v["gate"], "hold": None, "sample_at": None}
    return {"v": 1, "at": v["at"], "gate": v["gate"], "hold": v["hold"], "why": v["why"],
            "horizon_seconds": v["horizon"], "sample_at": v["sample_at"]}


# ---- the report ------------------------------------------------------------------------------------------------------

def _share(a, b):
    return round(a / b, 4) if b else None


# Written before any host data exists (review3 H-4; ADR-058): what a host's shadow evidence would have to show to
# support the owner turning predictive regulation on. It decides nothing; the owner does.
GUIDE = ("supports turning it on only with enough data (MIN_DAYS days and MIN_EPISODES breach episodes), at least 4 "
         "in 5 breach episodes preceded by a would-hold strictly before their first breach sample "
         "(preceded_before_start), and no more added false alarms than added hits (settled.added: holds where the "
         "real gate allowed and memory was not yet under the line)")


def guide(total, enough) -> dict:
    """GUIDE applied to the totals: supports_on True / False, or None without enough data."""
    if not enough:
        return {"supports_on": None, "rule": GUIDE}
    ok = total["preceded_before_start"] * 5 >= 4 * total["episodes"] and \
        total["added_false_alarms"] <= total["added_hits"]
    return {"supports_on": ok, "rule": GUIDE}


def report(db_path, now=None, *, setting=None, window=None, floor_bytes=None) -> dict:
    """The shadow evidence (see the module doc), read only: the roll-up and, settled in memory, the verdicts whose
    horizon has passed. `setting`: the owner's predictive_regulation, shown as given. window / floor_bytes: the
    benchmark's validation only (they apply to what is not in the roll-up yet)."""
    from .. import internal_state
    now = time.time() if now is None else now
    where = _folder(db_path)
    problems = []
    rollup, problem = _read_days(where, db_path, now) if where else ({"settled_until": None, "samples_until": None,
                                                                 "days": {}}, None)
    problems += [problem] if problem else []
    verdicts, ignored, problem = _read_verdicts(where, db_path, now) if where else ([], 0, None)
    problems += [problem] if problem else []
    points = _points(internal_state.history(db_path, limit=0, now=now)) if where else []
    pending = verdicts
    days = {d: dict(r) for d, r in rollup["days"].items()}
    if points and (rollup["settled_until"] is None or points[-1]["at"] > rollup["settled_until"]):
        fresh, pending, _ = _settle(verdicts, points, since=rollup["settled_until"], until=points[-1]["at"],
                                    samples_until=rollup["samples_until"], window=window, floor_bytes=floor_bytes)
        _merge(days, fresh)
    totals: dict = {}
    for row in days.values():
        _merge(totals, {"all": row})
    total = totals.get("all") or _empty_day()
    with_verdicts = sorted(d for d, r in days.items() if r["verdicts"])
    enough = len(with_verdicts) >= MIN_DAYS and total["episodes"] >= MIN_EPISODES
    settled = total["hits"] + total["false_alarms"] + total["misses"] + total["quiet"]
    lead = None
    if total["preceded"]:
        lead = {"mean": round(total["lead_seconds_sum"] / total["preceded"], 1),
                "min": total["lead_seconds_min"], "max": total["lead_seconds_max"]}
    statement = (f"These numbers rest on {len(with_verdicts)} day(s) with verdicts and {total['episodes']} memory "
                 f"breach episode(s) on this host. ")
    statement += ("That is enough to read them (at least " if enough else "Not enough data yet (at least ") + \
        f"{MIN_DAYS} days and {MIN_EPISODES} breach episodes are needed); that minimum only gates reading, it is " \
        "not a decision rule. What the forecast adds is under settled.added (only where the real gate allowed). " \
        "Verdicts exist only while the worker asks the gate; breach episodes are counted from every sample, also " \
        "when web research is disabled or writes are paused."
    return {"schema": SCHEMA, "at": now, "setting": setting,
            "enough_data": enough, "statement": statement,
            "rests_on": {"days_with_verdicts": len(with_verdicts), "first_day": with_verdicts[0] if with_verdicts
                         else None, "last_day": with_verdicts[-1] if with_verdicts else None,
                         "breach_episodes": total["episodes"], "minimum_days": MIN_DAYS,
                         "minimum_breach_episodes": MIN_EPISODES},
            "verdicts": {k: total[k] for k in ("verdicts", "holds", "allows", "no_forecast", "failed", "unsettled")},
            "settled": {"hits": total["hits"], "false_alarms": total["false_alarms"], "misses": total["misses"],
                        "quiet": total["quiet"], "total": settled,
                        "false_alarm_share": _share(total["false_alarms"], total["hits"] + total["false_alarms"]),
                        "by_reason": {"memory_holds": total["memory_holds"], "memory_hits": total["memory_hits"],
                                      "voice_holds": total["voice_holds"], "voice_followed": total["voice_followed"]},
                        "holds_gate_refused": total["holds_gate_refused"],
                        "added": {"hits": total["added_hits"], "false_alarms": total["added_false_alarms"],
                                  "misses": total["added_misses"], "quiet": total["added_quiet"],
                                  "false_alarm_share": _share(total["added_false_alarms"],
                                                              total["added_hits"] + total["added_false_alarms"])}},
            "breaches": {"samples": total["samples"], "breach_samples": total["breach_samples"],
                         "episodes": total["episodes"], "preceded": total["preceded"],
                         "preceded_before_start": total["preceded_before_start"],
                         "preceded_share": _share(total["preceded"], total["episodes"]), "lead_seconds": lead,
                         "lead_seconds_distribution": {name: total["lead " + name] for name, _ in LEAD_BUCKETS},
                         "lead_note": "a lead cannot exceed the verdict's horizon (180 s): a mean equal to it means "
                                      "every preceded episode was seen as early as the horizon allows"},
            "work": {"holds_the_gate_allowed": total["holds_gate_allowed"], "background_starts": total["starts"],
                     "starts_after_a_hold": total["starts_under_hold"],
                     "share_of_starts": _share(total["starts_under_hold"], total["starts"])},
            "guide": guide(total, enough),
            "pending": len(pending), "ignored_lines": ignored, "problems": problems,
            "definitions": {"breach": "a history sample with available memory under the critical line "
                                      "(resources.monitor.thresholds)" if floor_bytes is None else
                            f"a history sample with available memory under {floor_bytes} bytes",
                            "hit": "a would-hold verdict with a breach at its own sample or within its horizon (a "
                                   "note made while memory is already short counts; settled.added counts only the "
                                   "verdicts where the real gate allowed and memory was not under the line)",
                            "false_alarm": "a would-hold verdict with no breach within its horizon",
                            "miss": "a would-allow verdict followed by a breach within its horizon",
                            "work": "background starts after a would-hold verdict that the real gate allowed "
                                    "(net increase of running background tasks to the next sample)"},
            "days": days,
            "files": {"verdicts": str(where / FILE) if where else None,
                      "roll_up": str(where / DAYS_FILE) if where else None}}


__all__ = ["DAYS_FILE", "FILE", "MIN_DAYS", "MIN_EPISODES", "SETTLE_EVERY", "observe", "report", "settle_due"]
