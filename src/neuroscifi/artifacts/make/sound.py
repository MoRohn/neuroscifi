"""Sound from a specification (DESIGN §6, ADR-074 lane G2): tones, sequences and sonified series as 16-bit PCM WAV.

Three forms:
- `sound.tone`: one tone of a stated pitch and length.
- `sound.sequence`: notes (a pitch, or a rest) one after another, each followed by a short silence.
- `sound.sonify`: a series as equal-length tones whose pitch is linear in the value between two stated frequencies
  (the reading contract of DESIGN §6), optionally preceded by two reference tones (the lowest and the highest value
  of the axis); a missing value is a rest.

Every tone starts and ends with a raised-cosine ramp (no clicks); the loudest sample stays `headroom_db` below full
scale. The file carries its title and a plain description of how to hear it (RIFF `LIST/INFO`), and the same words
are returned as a note for a later wave's speech engine (text only: no voice engine is called here).

Deterministic on every host: the sine is computed with additions, multiplications and `floor` only (no library
`sin`, whose last bit may differ between machines), and nothing in the file depends on time or chance.
Nothing made here is evidence (COMMON, ADR-049, ADR-071).
"""
from __future__ import annotations

import math
import struct

import numpy as np

from ..spec import Spec
from ..types import Limits, Malformed, Refused

RATES = [8000, 16000, 22050, 32000, 44100, 48000]
HARD_MAX_DURATION_S = 600.0
SOFTWARE = "NeuroSciFi"
MADE_NOTE = "made by the system from a specification; not evidence"

_HISTORY = {"type": "array", "maxItems": 3, "items": {"type": "string", "maxLength": 300}}
_COMMON = {
    "rate_hz": {"type": "integer", "enum": RATES},
    "channels": {"type": "integer", "enum": [1, 2]},
    "headroom_db": {"type": "number", "minimum": 0.1, "maximum": 24},
    "ramp_s": {"type": "number", "minimum": 0.003, "maximum": 0.1},
    "max_duration_s": {"type": "number", "minimum": 0.1, "maximum": HARD_MAX_DURATION_S},
    "spoken": {"type": "string", "maxLength": 1000},
    "history": _HISTORY,
}
DEFAULTS = {"rate_hz": 44100, "channels": 1, "headroom_db": 1.0, "ramp_s": 0.005, "max_duration_s": 60.0,
            "gap_s": 0.04, "low_hz": 220.0, "high_hz": 880.0, "tone_s": 0.18, "reference": "ends", "pause_s": 0.3}


def _options(extra: dict) -> dict:
    return {"type": "object", "additionalProperties": False, "properties": {**_COMMON, **extra}}


SCHEMAS = {
    "sound.tone": {
        "data": {"type": "object", "additionalProperties": False, "required": ["frequency_hz", "duration_s"],
                 "properties": {"frequency_hz": {"type": "number", "minimum": 20, "maximum": 20000},
                                "duration_s": {"type": "number", "minimum": 0.02, "maximum": HARD_MAX_DURATION_S},
                                "amplitude": {"type": "number", "minimum": 0.01, "maximum": 1}}},
        "options": _options({}),
    },
    "sound.sequence": {
        "data": {"type": "object", "additionalProperties": False, "required": ["notes"],
                 "properties": {"notes": {"type": "array", "minItems": 1, "maxItems": 4000, "items": {
                     "type": "object", "additionalProperties": False, "required": ["frequency_hz", "duration_s"],
                     "properties": {"frequency_hz": {"type": ["number", "null"], "minimum": 20, "maximum": 20000},
                                    "duration_s": {"type": "number", "minimum": 0.02, "maximum": 60},
                                    "amplitude": {"type": "number", "minimum": 0.01, "maximum": 1}}}}}},
        "options": _options({"gap_s": {"type": "number", "minimum": 0.01, "maximum": 2}}),
    },
    "sound.sonify": {
        "data": {"type": "object", "additionalProperties": False, "required": ["values"],
                 "properties": {"values": {"type": "array", "minItems": 1, "maxItems": 10000,
                                           "items": {"type": ["number", "null"]}},
                                "name": {"type": "string", "maxLength": 100},
                                "unit": {"type": "string", "maxLength": 32}}},
        "options": _options({
            "low_hz": {"type": "number", "minimum": 100, "maximum": 4000},
            "high_hz": {"type": "number", "minimum": 100, "maximum": 8000},
            "tone_s": {"type": "number", "minimum": 0.05, "maximum": 2},
            "gap_s": {"type": "number", "minimum": 0.01, "maximum": 1},
            "pause_s": {"type": "number", "minimum": 0, "maximum": 2},
            "range": {"type": "array", "minItems": 2, "maxItems": 2, "items": {"type": "number"}},
            "reference": {"type": "string", "enum": ["none", "ends"]},
        }),
    },
}


# ---------------------------------------------------------------------------------------------------------------
# A sine every host computes to the same bits


_TWO_PI = 2.0 * math.pi
# Taylor coefficients of sin(x)/x in x², highest first; on |x| <= pi/2 the truncation error is below 1e-14.
_SIN_COEFFS = tuple((-1.0) ** k / math.factorial(2 * k + 1) for k in range(9, -1, -1))


def sin_turns(t: np.ndarray) -> np.ndarray:
    """sin(2*pi*t) from IEEE additions, multiplications and floor only (bitwise the same on every host)."""
    t = np.asarray(t, dtype=np.float64)
    t = t - np.floor(t + 0.5)                                   # [-0.5, 0.5)
    t = np.where(t > 0.25, 0.5 - t, np.where(t < -0.25, -0.5 - t, t))   # [-0.25, 0.25]: sin is odd and symmetric
    x = t * _TWO_PI
    x2 = x * x
    p = np.full_like(x, _SIN_COEFFS[0])
    for c in _SIN_COEFFS[1:]:
        p = p * x2 + c
    return x * p


def _tone(frequency: float, n: int, rate: int, ramp: int, amplitude: float) -> np.ndarray:
    k = np.arange(n, dtype=np.float64)
    wave = sin_turns(k * (frequency / rate))
    env = np.ones(n)
    r = min(ramp, n // 2)
    if r > 0:
        rise = sin_turns(np.arange(r, dtype=np.float64) / (4.0 * r)) ** 2    # sin^2(pi k / 2r): raised cosine
        env[:r] = rise
        env[n - r:] = rise[::-1]
    return wave * env * amplitude


# ---------------------------------------------------------------------------------------------------------------
# Planning a timeline from a spec


def _opt(spec: Spec, name: str):
    return spec.options.get(name, DEFAULTS.get(name))


def value_range(values, given=None) -> tuple[float, float]:
    """The axis of a sonified series: the spec's `range` if given, else the smallest and largest present value."""
    if given is not None:
        return float(given[0]), float(given[1])
    present = [float(v) for v in values if v is not None]
    if not present:
        return 0.0, 1.0
    return min(present), max(present)


def pitch_of(value: float, lo: float, hi: float, low_hz: float, high_hz: float) -> float:
    """Pitch linear in the value between the two stated frequencies; a flat axis sounds at the middle pitch."""
    if hi == lo:
        return (low_hz + high_hz) / 2.0
    share = min(1.0, max(0.0, (value - lo) / (hi - lo)))
    return low_hz + share * (high_hz - low_hz)


def _plan(spec: Spec) -> list[tuple[float | None, float, float]]:
    """The sound as (pitch or None for silence, seconds, amplitude) parts, in order."""
    form = spec.spec
    if form == "sound.tone":
        return [(float(spec.data["frequency_hz"]), float(spec.data["duration_s"]),
                 float(spec.data.get("amplitude", 1.0)))]
    gap = float(_opt(spec, "gap_s"))
    if form == "sound.sequence":
        parts = []
        for note in spec.data["notes"]:
            f = note["frequency_hz"]
            parts.append((None if f is None else float(f), float(note["duration_s"]), float(note.get("amplitude", 1.0))))
            parts.append((None, gap, 0.0))
        return parts
    low, high = float(_opt(spec, "low_hz")), float(_opt(spec, "high_hz"))
    if high <= low:
        raise Malformed("the highest pitch must be above the lowest")
    lo, hi = value_range(spec.data["values"], spec.options.get("range"))
    if hi < lo:
        raise Malformed("the range's first number must not exceed its second")
    tone = float(_opt(spec, "tone_s"))
    parts = []
    if _opt(spec, "reference") == "ends":
        parts += [(low, tone, 0.7), (None, gap, 0.0), (high, tone, 0.7), (None, gap + float(_opt(spec, "pause_s")), 0.0)]
    for v in spec.data["values"]:
        parts.append((None if v is None else pitch_of(float(v), lo, hi, low, high), tone, 1.0))
        parts.append((None, gap, 0.0))
    return parts


def duration_of(spec: Spec) -> float:
    return sum(seconds for _, seconds, _ in _plan(spec))


def listening_guide(spec: Spec) -> str:
    """How to hear the sound, in plain words (also the text a later wave's speech engine would read)."""
    if spec.options.get("spoken"):
        return str(spec.options["spoken"])
    form = spec.spec
    if form == "sound.tone":
        return f"One steady tone at {_fmt(spec.data['frequency_hz'])} hertz for {_fmt(spec.data['duration_s'])} seconds."
    if form == "sound.sequence":
        notes = spec.data["notes"]
        rests = sum(1 for n in notes if n["frequency_hz"] is None)
        text = f"{len(notes) - rests} notes one after another, each followed by a short silence."
        return text + (f" {rests} rests are silent." if rests else "")
    values = spec.data["values"]
    lo, hi = value_range(values, spec.options.get("range"))
    unit = (" " + spec.data["unit"]) if spec.data.get("unit") else ""
    name = spec.data.get("name") or "the series"
    text = (f"{len(values)} short tones, one for each value of {name}, in order. Higher pitch means a larger value: "
            f"the lowest pitch stands for {_fmt(lo)}{unit} and the highest for {_fmt(hi)}{unit}.")
    if _opt(spec, "reference") == "ends":
        text += " Two reference tones come first: the lowest pitch, then the highest."
    if any(v is None for v in values):
        text += " A silence in place of a tone is a missing value."
    return text


def _fmt(x) -> str:
    x = float(x)
    if x == int(x) and abs(x) < 1e15:
        return str(int(x))
    return f"{x:.6g}"


# ---------------------------------------------------------------------------------------------------------------
# Bytes


def _info_chunk(fields: list[tuple[bytes, str]]) -> bytes:
    body = b"INFO"
    for key, text in fields:
        raw = text.encode("utf-8") + b"\0"
        body += key + struct.pack("<I", len(raw)) + raw + (b"\0" if len(raw) % 2 else b"")
    return b"LIST" + struct.pack("<I", len(body)) + body


def wav_bytes(pcm: np.ndarray, rate: int, info: list[tuple[bytes, str]]) -> bytes:
    """A RIFF/WAVE file: `fmt ` (PCM, 16 bits), `data`, then `LIST/INFO`."""
    frames, channels = pcm.shape
    data = pcm.astype("<i2").tobytes()
    fmt = struct.pack("<HHIIHH", 1, channels, rate, rate * channels * 2, channels * 2, 16)
    body = (b"WAVE" + b"fmt " + struct.pack("<I", len(fmt)) + fmt
            + b"data" + struct.pack("<I", len(data)) + data + (b"\0" if len(data) % 2 else b"")
            + _info_chunk(info))
    return b"RIFF" + struct.pack("<I", len(body)) + body


def render(spec: Spec, limits: Limits = Limits()) -> tuple[bytes, tuple[str, ...]]:
    rate = int(_opt(spec, "rate_hz"))
    channels = int(_opt(spec, "channels"))
    ramp = int(round(float(_opt(spec, "ramp_s")) * rate))
    peak = 10.0 ** (-float(_opt(spec, "headroom_db")) / 20.0)
    parts = _plan(spec)
    total = sum(seconds for _, seconds, _ in parts)
    bound = min(float(_opt(spec, "max_duration_s")), HARD_MAX_DURATION_S)
    if total > bound + 1e-9:
        raise Refused(f"the sound would last {total:.1f} s, over the bound of {bound:g} s", rule="max_duration",
                      reason="too_large")
    counts = [int(round(seconds * rate)) for _, seconds, _ in parts]
    n_total = sum(counts)
    if n_total * channels * 2 + 4096 > limits.max_bytes:
        raise Refused("the sound would be larger than allowed", rule="max_bytes", reason="too_large")
    for f, seconds, _ in parts:
        if f is not None:
            if f > 0.45 * rate:
                raise Malformed(f"a pitch of {f:g} Hz cannot be carried at {rate} samples per second")
            if seconds < 4 * ramp / rate:
                raise Malformed("a tone is too short for its fade in and fade out")
    out = np.zeros(n_total, dtype=np.float64)
    at = 0
    for (f, _, amp), n in zip(parts, counts, strict=True):
        if f is not None:
            out[at:at + n] = _tone(f, n, rate, ramp, amp * peak)
        at += n
    pcm = np.round(out * 32767.0).astype(np.int16)
    pcm = np.repeat(pcm[:, None], channels, axis=1)
    guide = listening_guide(spec)
    info = [(b"INAM", spec.title[:200]), (b"ICMT", (guide + " " + MADE_NOTE[0].upper() + MADE_NOTE[1:] + ".")[:2000]),
            (b"ISFT", SOFTWARE)]
    notes = ("spoken: " + guide, MADE_NOTE, f"duration {total:.3f} s at {rate} samples per second")
    return wav_bytes(pcm, rate, info), notes


# ---------------------------------------------------------------------------------------------------------------
# Repair: a sonified series over the duration bound is resampled by a stated rule


def bucket_means(values: list, n: int) -> list:
    """`values` cut into `n` consecutive runs as equal as possible; each run becomes the mean of its present values
    (a run of only missing values stays missing)."""
    out = []
    size = len(values)
    for k in range(n):
        run = [float(v) for v in values[k * size // n:(k + 1) * size // n] if v is not None]
        out.append(math.fsum(run) / len(run) if run else None)
    return out


def repair(value: dict, problem) -> dict | None:
    """One bounded repair for a `sound.sonify` spec that is over its duration bound: the series is resampled to as
    many tones as fit, each the mean of consecutive values, and the description says so. Any other problem: None."""
    if value.get("spec") != "sound.sonify":
        return None
    over = getattr(problem, "rule", None) == "max_duration" or any(
        getattr(f, "rule", None) == "duration_bound" and not f.passed for f in getattr(problem, "technical", ()))
    if not over:
        return None
    options = dict(value.get("options", {}))
    history = list(options.get("history", []))
    if len(history) >= 3:
        return None
    get = lambda name: options.get(name, DEFAULTS[name])   # noqa: E731
    slot = float(get("tone_s")) + float(get("gap_s"))
    lead = (2 * slot + float(get("pause_s"))) if get("reference") == "ends" else 0.0
    bound = min(float(get("max_duration_s")), HARD_MAX_DURATION_S)
    values = list(value["data"]["values"])
    fit = int(math.floor((bound - lead) / slot + 1e-9))
    if fit < 2 or fit >= len(values):
        return None
    if "range" not in options:      # keep the axis of the whole series, not of the means
        lo, hi = value_range(values)
        options["range"] = [lo, hi]
    per = len(values) / fit
    rule = (f"shortened to fit {bound:g} seconds: {len(values)} values became {fit} tones, each the mean of about "
            f"{per:.3g} consecutive values")
    options["history"] = history + [rule]
    description = (value.get("description", "") + " " + rule[0].upper() + rule[1:] + ".").strip()[:2000]
    return {**value, "description": description, "options": options,
            "data": {**value["data"], "values": bucket_means(values, fit)}}


__all__ = ["DEFAULTS", "SCHEMAS", "bucket_means", "duration_of", "listening_guide", "pitch_of", "render", "repair",
           "sin_turns", "value_range", "wav_bytes"]
