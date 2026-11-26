"""Measurements computed from a decoded `Signal`'s samples (no claim about what the sound is: never "speech" or
"music"). Each is a named function with a stated method; `analyse()` gathers them into a bounded, JSON-able dict.

Spectral measures use the mono mix (the mean of the channels), a Hann window of N_FFT samples (2048 by default)
and a hop of N_FFT / 4, computed block by block so that memory stays bounded for long signals.
"""
from __future__ import annotations

import numpy as np

from .types import Signal

N_FFT = 2048
ONSET_FFT = 512             # onsets use a shorter window for time resolution; times are window centres
CLIP_LEVEL = 0.999          # |sample| at or above this counts as clipped
SILENCE_DB = -50.0          # frame RMS below this (dBFS) is silent
SILENCE_MIN_S = 0.1         # shortest silent stretch reported
FLUX_K = 1.5                # onset: flux above local median + FLUX_K x local MAD
TEMPO_MIN_ONSETS = 8
TEMPO_MIN_CONFIDENCE = 0.3
MAX_LIST = 64


def mono(sig: Signal) -> np.ndarray:
    return sig.samples.astype(np.float64).mean(axis=1) if sig.samples.size else np.zeros(0)


def duration(sig: Signal) -> float:
    return sig.samples.shape[0] / sig.rate_hz


def peak(sig: Signal) -> list[float]:
    """Largest absolute sample per channel."""
    if not sig.samples.size:
        return [0.0] * sig.samples.shape[1]
    return [float(v) for v in np.abs(sig.samples).max(axis=0)]


def rms(sig: Signal) -> list[float]:
    if not sig.samples.size:
        return [0.0] * sig.samples.shape[1]
    return [float(v) for v in np.sqrt(np.mean(sig.samples.astype(np.float64) ** 2, axis=0))]


def clipping_share(sig: Signal, level: float = CLIP_LEVEL) -> float:
    """Share of samples (all channels) with |x| >= level."""
    return float((np.abs(sig.samples) >= level).mean()) if sig.samples.size else 0.0


def dc_offset(sig: Signal) -> list[float]:
    """Mean sample value per channel."""
    if not sig.samples.size:
        return [0.0] * sig.samples.shape[1]
    return [float(v) for v in sig.samples.astype(np.float64).mean(axis=0)]


def zero_crossing_rate(sig: Signal) -> float:
    """Sign changes of the mono mix per second."""
    x = mono(sig)
    if len(x) < 2:
        return 0.0
    s = np.signbit(x)
    return float(np.count_nonzero(s[1:] != s[:-1]) / duration(sig))


def _frame_rms_db(x: np.ndarray, frame: int) -> np.ndarray:
    n = len(x) // frame
    if n == 0:
        return np.zeros(0)
    r = np.sqrt(np.mean(x[:n * frame].reshape(n, frame) ** 2, axis=1))
    return 20 * np.log10(np.maximum(r, 1e-12))


def silence_segments(sig: Signal, threshold_db: float = SILENCE_DB, min_s: float = SILENCE_MIN_S,
                     frame_s: float = 0.02) -> list[tuple[float, float]]:
    """(start_s, end_s) of stretches whose 20 ms frame RMS of the mono mix stays below `threshold_db` dBFS for at
    least `min_s` seconds."""
    frame = max(1, int(round(frame_s * sig.rate_hz)))
    db = _frame_rms_db(mono(sig), frame)
    quiet = np.concatenate([[False], db < threshold_db, [False]])
    edges = np.flatnonzero(quiet[1:] != quiet[:-1])
    out = []
    for start, end in zip(edges[::2], edges[1::2]):
        if (end - start) * frame / sig.rate_hz >= min_s:
            out.append((start * frame / sig.rate_hz, end * frame / sig.rate_hz))
    return out


def _stft_blocks(x: np.ndarray, n_fft: int, hop: int, block: int = 256):
    """Magnitude spectra (frames, n_fft // 2 + 1), `block` frames at a time."""
    if len(x) < n_fft:
        x = np.concatenate([x, np.zeros(n_fft - len(x))])
    n_frames = 1 + (len(x) - n_fft) // hop
    window = np.hanning(n_fft)
    for start in range(0, n_frames, block):
        idx = np.arange(start, min(n_frames, start + block))[:, None] * hop + np.arange(n_fft)[None, :]
        yield start, np.abs(np.fft.rfft(x[idx] * window, axis=1))


def spectral_frames(sig: Signal, n_fft: int = N_FFT) -> dict:
    """Per frame: spectral flux (half-wave rectified magnitude increase), spectral centroid (Hz) and the frequency
    of the strongest bin (Hz); plus the mean magnitude spectrum. Frame times are window starts."""
    x, hop = mono(sig), n_fft // 4
    freqs = np.fft.rfftfreq(n_fft, 1.0 / sig.rate_hz)
    flux, centroid, strongest, total, prev, frames = [], [], [], None, None, 0
    for _, mag in _stft_blocks(x, n_fft, hop):
        if prev is None:
            prev = mag[:1]
        diff = np.diff(np.concatenate([prev, mag]), axis=0)
        flux.append(np.maximum(diff, 0).sum(axis=1))
        energy = mag.sum(axis=1)
        centroid.append(np.where(energy > 0, (mag * freqs).sum(axis=1) / np.maximum(energy, 1e-12), 0.0))
        strongest.append(freqs[mag.argmax(axis=1)])
        total = mag.sum(axis=0) if total is None else total + mag.sum(axis=0)
        frames += len(mag)
        prev = mag[-1:]
    return {"hop_s": hop / sig.rate_hz, "n_fft": n_fft, "bin_hz": sig.rate_hz / n_fft,
            "flux": np.concatenate(flux), "centroid_hz": np.concatenate(centroid),
            "strongest_hz": np.concatenate(strongest), "mean_spectrum": total / max(frames, 1), "freqs": freqs}


def spectral_centroid(sig: Signal) -> float:
    """Mean over frames of the magnitude-weighted mean frequency (Hz), frames with energy only."""
    sf = spectral_frames(sig)
    c = sf["centroid_hz"][sf["centroid_hz"] > 0]
    return float(c.mean()) if c.size else 0.0


def onsets(sig: Signal, k: float = FLUX_K) -> list[float]:
    """Onset times (s) by spectral flux on ONSET_FFT-sample Hann windows (hop ONSET_FFT / 4): frames whose flux is
    a local maximum above median + k x MAD of the flux within +-0.5 s, and above 1 % of the largest flux; at most
    one onset per 50 ms. The time reported is the window's centre."""
    sf = spectral_frames(sig, n_fft=ONSET_FFT)
    flux, hop_s = sf["flux"], sf["hop_s"]
    centre = ONSET_FFT / 2 / sig.rate_hz
    if len(flux) < 3 or flux.max() <= 0:
        return []
    half = max(1, int(round(0.5 / hop_s)))
    out, last = [], -1.0
    floor = 0.01 * flux.max()
    for i in range(1, len(flux) - 1):
        if not (flux[i] > flux[i - 1] and flux[i] >= flux[i + 1] and flux[i] > floor):
            continue
        window = flux[max(0, i - half):i + half + 1]
        med = np.median(window)
        mad = np.median(np.abs(window - med))
        t = i * hop_s + centre
        if flux[i] > med + k * mad and t - last >= 0.05:
            out.append(round(t, 6))
            last = t
    return out


def dominant_frequency(sig: Signal, segments: int = 8) -> list[dict]:
    """For `segments` equal parts of the signal: the frequency (Hz) of the largest bin of the mean magnitude
    spectrum, to the resolution rate / N_FFT. Silent parts give None."""
    x = mono(sig)
    out = []
    n = len(x)
    for s in range(segments):
        a, b = n * s // segments, n * (s + 1) // segments
        part = Signal(x[a:b, None].astype(np.float32), sig.rate_hz, ("mono",)) if b > a else None
        if part is None or not np.any(part.samples):
            out.append({"start_s": a / sig.rate_hz, "end_s": b / sig.rate_hz, "hz": None})
            continue
        sf = spectral_frames(part)
        out.append({"start_s": a / sig.rate_hz, "end_s": b / sig.rate_hz,
                    "hz": float(sf["freqs"][int(sf["mean_spectrum"].argmax())])})
    return out


def tempo(sig: Signal, onset_times: list[float] | None = None) -> dict:
    """Tempo (beats per minute) from the autocorrelation of the onset train, searched in 40-240 BPM.
    Rule: reported only when there are at least TEMPO_MIN_ONSETS onsets and the autocorrelation peak is at least
    TEMPO_MIN_CONFIDENCE of the zero-lag value; otherwise `bpm` is None and `reason` says why."""
    times = onsets(sig) if onset_times is None else onset_times
    if len(times) < TEMPO_MIN_ONSETS:
        return {"bpm": None, "confidence": 0.0, "reason": f"fewer than {TEMPO_MIN_ONSETS} onsets"}
    res = 0.01
    train = np.zeros(int(times[-1] / res) + 2)
    train[np.rint(np.asarray(times) / res).astype(int)] = 1.0
    train = np.convolve(train, np.ones(3), mode="same")       # tolerate +-10 ms jitter
    ac = np.correlate(train, train, mode="full")[len(train) - 1:]
    lo, hi = int(60 / 240 / res), int(60 / 40 / res)
    if hi >= len(ac):
        return {"bpm": None, "confidence": 0.0, "reason": "the onsets span too short a time"}
    lag = lo + int(ac[lo:hi + 1].argmax())
    confidence = float(ac[lag] / ac[0]) if ac[0] > 0 else 0.0
    if confidence < TEMPO_MIN_CONFIDENCE:
        return {"bpm": None, "confidence": round(confidence, 3),
                "reason": f"autocorrelation peak below {TEMPO_MIN_CONFIDENCE}"}
    return {"bpm": round(60 / (lag * res), 2), "confidence": round(confidence, 3), "reason": "rule met"}


def short_time_spectrum(sig: Signal, n_times: int = 64, n_bands: int = 32) -> dict:
    """A coarse spectrogram: mean magnitude (dB) in `n_bands` equal-width bands for `n_times` equal time slices."""
    sf_blocks = []
    x, hop = mono(sig), N_FFT // 4
    for _, mag in _stft_blocks(x, N_FFT, hop):
        bands = np.array_split(mag, n_bands, axis=1)
        sf_blocks.append(np.stack([b.mean(axis=1) for b in bands], axis=1))
    grid = np.concatenate(sf_blocks) if sf_blocks else np.zeros((0, n_bands))
    slices = np.array_split(grid, min(n_times, max(1, len(grid))), axis=0)
    db = [[round(float(20 * np.log10(max(v, 1e-12))), 2) for v in s.mean(axis=0)] for s in slices if len(s)]
    return {"window": f"Hann, {N_FFT} samples, hop {N_FFT // 4}", "band_hz": sig.rate_hz / 2 / n_bands,
            "db": db}


def analyse(sig: Signal) -> dict:
    """All measurements above, bounded (lists cut to MAX_LIST items)."""
    on = onsets(sig)
    return {"duration_s": duration(sig), "rate_hz": sig.rate_hz, "channels": sig.samples.shape[1],
            "peak": peak(sig), "rms": rms(sig), "clipping_share": clipping_share(sig), "dc_offset": dc_offset(sig),
            "zero_crossing_rate": zero_crossing_rate(sig), "spectral_centroid_hz": spectral_centroid(sig),
            "silence": silence_segments(sig)[:MAX_LIST], "onsets_s": on[:MAX_LIST], "n_onsets": len(on),
            "dominant_frequency": dominant_frequency(sig), "tempo": tempo(sig, on),
            "spectrum": short_time_spectrum(sig, 16, 16)}


__all__ = ["CLIP_LEVEL", "N_FFT", "ONSET_FFT", "analyse", "clipping_share", "dc_offset", "dominant_frequency", "duration",
           "mono", "onsets", "peak", "rms", "short_time_spectrum", "silence_segments", "spectral_centroid",
           "spectral_frames", "tempo", "zero_crossing_rate"]
