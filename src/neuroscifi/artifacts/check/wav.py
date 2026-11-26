"""An independent check of WAV sound (DESIGN §6, ADR-074 lane G2): its own RIFF reader, measurements, and the
recovery of a sonified series from the pitch of each tone.

Written from the RIFF/WAVE format and the reading contract ("a sonified series is a sequence of equal-length tones,
pitch linear in the value between the two frequencies stated in the spec"); it imports nothing from `make`.

Technical findings (they decide `ok`):
- `wav_structure`: RIFF/WAVE header, chunk sizes inside the file, a `fmt ` chunk before `data`.
- `pcm16`: uncompressed PCM, 16 bits per sample.
- `data_complete`: the `data` chunk holds every byte it declares (a truncated file is caught here).
- `no_clipping`: no run of 3 or more samples at full scale (|s| >= 32766).
- `dc_offset`: the mean of each channel is within 1 % of full scale of zero.
- `no_clicks`: each sounding segment starts and ends quietly. Measure: the largest |sample| in the first and the
  last 0.5 ms of a segment divided by the segment's peak; above 0.25 is a click (a raised-cosine fade of 3 ms or
  more stays below 0.17; a tone of 100 Hz or more cut without a fade reaches at least 0.3).
With a spec: `duration` (within 5 ms), `segments` (one sounding segment per tone the spec calls for),
`values_recovered` (share of values whose tone's measured pitch is within 1 % of the frequency span of the pitch
the value calls for), `order_preserved`, and `reference_tones` when the spec asks for them.
Style finding (never decides `ok`): `listening_guide` (the file says in words how to hear it).
"""
from __future__ import annotations

import math
import struct

import numpy as np

from ..types import Limits
from . import Finding, Verdict

FULL = 32767.0
CLIP_LEVEL = 32766
EDGE_S = 0.0005
CLICK_RATIO = 0.25
PITCH_TOLERANCE = 0.01          # of the span between the two stated frequencies
DURATION_TOLERANCE_S = 0.005
MAX_CHUNKS = 64

# The reading contract's defaults (the spec states them; these are what a spec that leaves them out means).
_DEFAULTS = {"rate_hz": 44100, "channels": 1, "gap_s": 0.04, "low_hz": 220.0, "high_hz": 880.0, "tone_s": 0.18,
             "reference": "ends", "pause_s": 0.3, "max_duration_s": 60.0}


class _Wav:
    def __init__(self):
        self.problems: list[str] = []
        self.format_tag = None
        self.channels = 0
        self.rate = 0
        self.bits = 0
        self.declared = 0           # bytes the data chunk says it holds
        self.present = 0            # bytes of it that are really there
        self.samples = np.zeros((0, 1), dtype=np.float64)
        self.info: dict[str, str] = {}


def read(data: bytes, limits: Limits = Limits()) -> _Wav:
    """Walk the RIFF chunks with every size checked against the bytes really present."""
    w = _Wav()
    if len(data) > limits.max_bytes:
        w.problems.append("larger than the size limit")
        return w
    if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        w.problems.append("not a RIFF/WAVE file")
        return w
    riff_size = struct.unpack_from("<I", data, 4)[0]
    if riff_size + 8 != len(data):
        w.problems.append(f"the RIFF size says {riff_size + 8} bytes, the file has {len(data)}")
    pos, fmt_seen, data_at = 12, False, None
    for _ in range(MAX_CHUNKS):
        if pos + 8 > len(data):
            break
        cid, size = data[pos:pos + 4], struct.unpack_from("<I", data, pos + 4)[0]
        body = pos + 8
        end = body + size
        if cid == b"fmt ":
            if size < 16 or end > len(data):
                w.problems.append("the fmt chunk is short")
                return w
            w.format_tag, w.channels, w.rate, byte_rate, align, w.bits = struct.unpack_from("<HHIIHH", data, body)
            if w.format_tag == 0xFFFE and size >= 40:          # WAVE_FORMAT_EXTENSIBLE: the sub-format decides
                w.format_tag = struct.unpack_from("<H", data, body + 24)[0]
            if w.channels < 1 or w.channels > 8 or not 1000 <= w.rate <= 384000:
                w.problems.append("the fmt chunk states an impossible layout")
                return w
            if align != w.channels * ((w.bits + 7) // 8) or byte_rate != w.rate * align:
                w.problems.append("the fmt chunk's block size or byte rate do not agree with its layout")
            fmt_seen = True
        elif cid == b"data":
            if not fmt_seen:
                w.problems.append("the data chunk comes before the fmt chunk")
                return w
            data_at = body
            w.declared = size
            w.present = max(0, min(size, len(data) - body))
        elif cid == b"LIST" and end <= len(data) and data[body:body + 4] == b"INFO":
            _read_info(data[body + 4:end], w.info)
        if end > len(data):
            if cid != b"data":
                w.problems.append(f"a chunk declares {size} bytes past the end of the file")
            break
        pos = end + (size & 1)
    else:
        w.problems.append(f"more than {MAX_CHUNKS} chunks")
    if not fmt_seen:
        w.problems.append("no fmt chunk")
        return w
    if data_at is None:
        w.problems.append("no data chunk")
        return w
    if w.format_tag == 1 and w.bits == 16:
        frames = w.present // (2 * w.channels)
        if frames * w.channels * 2 > limits.max_decoded_bytes:
            w.problems.append("more samples than the decode limit")
            return w
        raw = np.frombuffer(data, dtype="<i2", count=frames * w.channels, offset=data_at)
        w.samples = raw.reshape(frames, w.channels).astype(np.float64)
    return w


def _read_info(block: bytes, info: dict) -> None:
    pos = 0
    for _ in range(32):
        if pos + 8 > len(block):
            return
        key, size = block[pos:pos + 4], struct.unpack_from("<I", block, pos + 4)[0]
        raw = block[pos + 8:pos + 8 + min(size, 4096)]
        info[key.decode("latin-1")] = raw.split(b"\0", 1)[0].decode("utf-8", "replace")
        pos += 8 + size + (size & 1)


# ---------------------------------------------------------------------------------------------------------------
# Measurements


def segments(mono: np.ndarray, rate: int) -> list[tuple[int, int]]:
    """Sounding stretches: samples above 1 % of the file's peak, with quiet runs shorter than 5 ms bridged."""
    if not len(mono):
        return []
    peak = float(np.abs(mono).max())
    if peak == 0:
        return []
    loud = np.flatnonzero(np.abs(mono) > 0.01 * peak)
    if not len(loud):
        return []
    bridge = max(1, int(0.005 * rate))
    breaks = np.flatnonzero(np.diff(loud) > bridge)
    starts = np.concatenate([[loud[0]], loud[breaks + 1]])
    ends = np.concatenate([loud[breaks], [loud[-1]]]) + 1
    return [(int(a), int(b)) for a, b in zip(starts, ends, strict=True)]


def pitch(mono: np.ndarray, rate: int) -> float:
    """The frequency of the strongest spectral peak of the middle 80 % of a segment (Hann window, zero-padded FFT,
    parabolic interpolation on the log magnitude)."""
    n = len(mono)
    cut = mono[n // 10: n - n // 10] if n >= 20 else mono
    if len(cut) < 8:
        return float("nan")
    window = np.hanning(len(cut))
    size = 1 << int(math.ceil(math.log2(len(cut) * 8)))
    spectrum = np.abs(np.fft.rfft(cut * window, size))
    lowest = max(1, int(20.0 * size / rate))
    k = lowest + int(np.argmax(spectrum[lowest:-1]))
    a, b, c = np.log(spectrum[k - 1:k + 2] + 1e-300)
    denom = a - 2 * b + c
    delta = 0.5 * (a - c) / denom if denom != 0 else 0.0
    return (k + delta) * rate / size


def _click_ratio(mono: np.ndarray, start: int, end: int, rate: int) -> float:
    edge = max(1, int(round(EDGE_S * rate)))
    seg = np.abs(mono[start:end])
    peak = float(seg.max()) if len(seg) else 0.0
    if peak == 0:
        return 0.0
    return float(max(seg[:edge].max(), seg[-edge:].max()) / peak)


# ---------------------------------------------------------------------------------------------------------------
# What the spec calls for (derived here from the reading contract, not from the generator)


def _expected(spec, source) -> dict | None:
    form = getattr(spec, "spec", None)
    if form not in ("sound_tone", "sound_sequence", "sound_sonify"):
        return None
    opt = lambda name: spec.options.get(name, _DEFAULTS.get(name))   # noqa: E731
    out = {"rate": int(opt("rate_hz")), "channels": int(opt("channels")), "tones": [], "references": [],
           "values": None, "span_hz": None, "max_duration": float(opt("max_duration_s"))}
    if form == "sound_tone":
        out["tones"] = [float(spec.data["frequency_hz"])]
        out["duration"] = float(spec.data["duration_s"])
        out["span_hz"] = float(spec.data["frequency_hz"])
        return out
    gap = float(opt("gap_s"))
    if form == "sound_sequence":
        notes = spec.data["notes"]
        out["tones"] = [float(n["frequency_hz"]) for n in notes if n["frequency_hz"] is not None]
        out["duration"] = sum(float(n["duration_s"]) + gap for n in notes)
        present = out["tones"] or [1.0]
        out["span_hz"] = max(present) - min(present) or max(present)
        return out
    values = list(source) if source is not None else list(spec.data["values"])
    low, high, tone = float(opt("low_hz")), float(opt("high_hz")), float(opt("tone_s"))
    given = spec.options.get("range")
    present = [float(v) for v in values if v is not None and math.isfinite(float(v))]
    lo, hi = (float(given[0]), float(given[1])) if given else ((min(present), max(present)) if present else (0.0, 1.0))
    lead = 0.0
    if opt("reference") == "ends":
        out["references"] = [low, high]
        lead = 2 * (tone + gap) + float(opt("pause_s"))
    out.update(values=values, lo=lo, hi=hi, low=low, high=high, span_hz=high - low,
               duration=lead + len(values) * (tone + gap))
    return out


def _value_of(freq: float, e: dict) -> float:
    if e["hi"] == e["lo"]:
        return e["lo"]
    return e["lo"] + (freq - e["low"]) / (e["high"] - e["low"]) * (e["hi"] - e["lo"])


def check_wav(data: bytes, media_type: str = "audio/wav", spec=None, source=None) -> Verdict:
    w = read(data)
    technical: list[Finding] = []
    style: list[Finding] = []
    structural = [p for p in w.problems if "RIFF size" not in p and "block size" not in p]
    technical.append(Finding("wav_structure", not w.problems,
                             "the RIFF chunks are sound" if not w.problems else "; ".join(w.problems[:3])))
    if structural and w.format_tag is None:
        return Verdict.of(technical, style)
    is_pcm16 = w.format_tag == 1 and w.bits == 16
    technical.append(Finding("pcm16", is_pcm16, f"format {w.format_tag}, {w.bits} bits per sample, "
                                                f"{w.channels} channel(s), {w.rate} samples per second"))
    complete = w.present == w.declared
    technical.append(Finding("data_complete", complete,
                             "the data chunk holds every byte it declares" if complete else
                             f"the data chunk declares {w.declared} bytes but only {w.present} are present",
                             measure=(w.present / w.declared) if w.declared else 1.0))
    if not is_pcm16:
        return Verdict.of(technical, style)
    s = w.samples
    rate = w.rate
    clipped = 0
    for ch in range(s.shape[1]):
        at_full = np.abs(s[:, ch]) >= CLIP_LEVEL
        if at_full.any():
            edges = np.diff(np.concatenate([[0], at_full.astype(np.int8), [0]]))
            runs = np.flatnonzero(edges == -1) - np.flatnonzero(edges == 1)
            clipped += int((runs >= 3).sum())
    peak = float(np.abs(s).max()) / FULL if s.size else 0.0
    peak_db = 20 * math.log10(peak) if peak > 0 else float("-inf")
    technical.append(Finding("no_clipping", clipped == 0, f"{clipped} run(s) of 3 or more samples at full scale; "
                                                          f"peak {peak_db:.2f} dB below full scale" if peak > 0 else
                             "silent file", measure=float(clipped)))
    dc = float(np.abs(s.mean(axis=0)).max()) / FULL if s.size else 0.0
    technical.append(Finding("dc_offset", dc <= 0.01, f"largest channel mean {dc:.4f} of full scale", measure=dc))
    mono = s.mean(axis=1) if s.size else np.zeros(0)
    segs = segments(mono, rate)
    ratios = [_click_ratio(mono, a, b, rate) for a, b in segs]
    worst = max(ratios) if ratios else 0.0
    technical.append(Finding("no_clicks", worst <= CLICK_RATIO,
                             f"{len(segs)} sounding segment(s); the loudest segment edge is {worst:.3f} of its "
                             f"segment's peak (above {CLICK_RATIO} is a click)", measure=worst))
    guide = w.info.get("ICMT", "")
    style.append(Finding("listening_guide", bool(guide.strip()),
                         "the file says in words how to hear it" if guide.strip() else "no description in the file"))
    e = _expected(spec, source)
    if e is None:
        style.append(Finding("values_not_compared", True, "no sound spec was given, so no values were compared"))
        return Verdict.of(technical, style)
    duration = len(s) / rate if rate else 0.0
    technical.append(Finding("layout_matches_spec", rate == e["rate"] and w.channels == e["channels"],
                             f"{rate} samples per second and {w.channels} channel(s); the spec asks for "
                             f"{e['rate']} and {e['channels']}"))
    technical.append(Finding("duration", abs(duration - e["duration"]) <= DURATION_TOLERANCE_S,
                             f"{duration:.4f} s; the spec calls for {e['duration']:.4f} s", measure=duration))
    technical.append(Finding("duration_bound", duration <= e["max_duration"] + DURATION_TOLERANCE_S,
                             f"{duration:.2f} s against a bound of {e['max_duration']:g} s"))
    measured = [pitch(mono[a:b], rate) for a, b in segs]
    refs = e["references"]
    if e["values"] is None:
        expected = e["tones"]
        technical.append(Finding("segments", len(measured) == len(expected),
                                 f"{len(measured)} sounding segment(s); the spec calls for {len(expected)}"))
        tol = PITCH_TOLERANCE * e["span_hz"]
        hits = sum(1 for m, x in zip(measured, expected, strict=False) if abs(m - x) <= max(tol, 1.0))
        share = hits / len(expected) if expected else 1.0
        technical.append(Finding("values_recovered", hits == len(expected) and len(measured) == len(expected),
                                 f"{hits} of {len(expected)} tones at the pitch the spec states", measure=share))
        return Verdict.of(technical, style)
    present = [float(v) for v in e["values"] if v is not None]
    technical.append(Finding("segments", len(measured) == len(refs) + len(present),
                             f"{len(measured)} sounding segment(s); the spec calls for {len(refs)} reference tone(s) "
                             f"and {len(present)} value tone(s)"))
    tol_hz = PITCH_TOLERANCE * e["span_hz"]
    if refs:
        got = measured[:2]
        ok = len(got) == 2 and all(abs(m - x) <= tol_hz for m, x in zip(got, refs, strict=True))
        technical.append(Finding("reference_tones", ok, "the two reference tones are at the lowest and highest pitch"
                                 if ok else "the reference tones are missing or at the wrong pitch"))
    recovered = [_value_of(m, e) for m in measured[len(refs):]]
    tol_value = tol_hz / e["span_hz"] * (e["hi"] - e["lo"]) if e["hi"] != e["lo"] else 0.0
    clamp = lambda v: min(max(v, e["lo"]), e["hi"])   # noqa: E731  values outside a stated range sound at its ends
    hits = sum(1 for r, x in zip(recovered, present, strict=False) if abs(r - clamp(x)) <= tol_value + 1e-12)
    share = hits / len(present) if present else 1.0
    technical.append(Finding("values_recovered", hits == len(present) and len(recovered) == len(present),
                             f"{hits} of {len(present)} values recovered from pitch within "
                             f"{PITCH_TOLERANCE:.0%} of the frequency span", measure=share))
    order = len(recovered) == len(present) and all(
        (a < b) == (clamp(x) < clamp(y)) for (a, b), (x, y) in zip(zip(recovered, recovered[1:], strict=False),
                                                                     zip(present, present[1:], strict=False),
                                                                     strict=True)
        if abs(clamp(x) - clamp(y)) > 2 * tol_value)
    technical.append(Finding("order_preserved", order, "every rise and fall of the series is heard in order"
                             if order else "rises and falls of the series are not heard in order"))
    return Verdict.of(technical, style)


__all__ = ["check_wav", "pitch", "read", "segments"]
