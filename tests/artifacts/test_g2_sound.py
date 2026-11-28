"""Lane G2: sound forms (make/sound.py) and the independent WAV check (check/wav.py).

Wrong artifacts are built here by hand (struct + numpy.sin), never by the generator, so that the checker is judged
against a construction it shares no code with."""
import struct

import numpy as np
import pytest

from neuroscifi.artifacts import check, make
from neuroscifi.artifacts.check import _g2 as _checks  # noqa: F401  registers the validators
from neuroscifi.artifacts.check import wav as wavcheck
from neuroscifi.artifacts.make import _g2
from neuroscifi.artifacts.make import sound
from neuroscifi.artifacts.spec import Spec
from neuroscifi.artifacts.types import Malformed, Refused

RATE = 44100


def _spec(form, data, options=None, title="A sound", description="Made for a test."):
    return {"spec": form, "version": 1, "title": title, "description": description, "data": data,
            "options": options or {}, "sources": ["request"], "made_by": {"generator": "", "version": 0}}


SERIES = [3, 5.5, None, 9, 2, 7.25, 1, 10]


def _failed(verdict):
    return {f.rule for f in verdict.technical if not f.passed}


def _hand_wav(samples, rate=RATE, fmt_tag=1, bits=16, channels=1, declared=None):
    """A WAV file assembled here with struct, from int16 (or other) samples."""
    data = np.asarray(samples).astype("<i2").tobytes() if bits == 16 else np.asarray(samples).astype("<f4").tobytes()
    size = len(data) if declared is None else declared
    align = channels * bits // 8
    fmt = struct.pack("<HHIIHH", fmt_tag, channels, rate, rate * align, align, bits)
    body = b"WAVE" + b"fmt " + struct.pack("<I", 16) + fmt + b"data" + struct.pack("<I", size) + data
    return b"RIFF" + struct.pack("<I", len(body)) + body


def _hand_tones(freqs, tone_s=0.18, gap_s=0.04, lead=(), ramp_s=0.005, amp=0.8, pause_s=0.3):
    """The reading contract's timeline built independently: [reference tones, pause], then tone + gap per value."""
    out = []
    n_tone, n_gap, r = round(tone_s * RATE), round(gap_s * RATE), round(ramp_s * RATE)
    env = np.ones(n_tone)
    if r:
        env[:r] = 0.5 - 0.5 * np.cos(np.pi * np.arange(r) / r)
        env[-r:] = env[:r][::-1]
    t = np.arange(n_tone) / RATE

    def tone(f):
        return np.zeros(n_tone) if f is None else amp * env * np.sin(2 * np.pi * f * t)
    for f in lead:
        out += [tone(f), np.zeros(n_gap)]
    if lead:
        out.append(np.zeros(round(pause_s * RATE)))
    for f in freqs:
        out += [tone(f), np.zeros(n_gap)]
    return np.round(np.concatenate(out) * 32767).astype(np.int16)


def _contract_pitch(v, lo=1.0, hi=10.0, low=220.0, high=880.0):
    return None if v is None else low + (v - lo) / (hi - lo) * (high - low)


def test_sonify_made_and_checked_with_every_value_recovered():
    made = make.make(_spec("sound.sonify", {"values": SERIES, "name": "rainfall", "unit": "mm"}))
    assert made.media_type == "audio/wav"
    verdict = check.check(made.bytes, made.media_type, made.spec)
    assert verdict.ok, _failed(verdict)
    rec = next(f for f in verdict.technical if f.rule == "values_recovered")
    assert rec.measure == 1.0
    assert any(n.startswith("spoken: ") and "Higher pitch means a larger value" in n for n in made.notes)
    assert wavcheck.read(made.bytes).info["ICMT"].startswith("8 short tones")


def test_tone_sequence_and_stereo_pass():
    for form, data in (("sound.tone", {"frequency_hz": 440, "duration_s": 0.5}),
                       ("sound.sequence", {"notes": [{"frequency_hz": 262, "duration_s": 0.3},
                                                     {"frequency_hz": None, "duration_s": 0.2},
                                                     {"frequency_hz": 392, "duration_s": 0.3}]})):
        for channels in (1, 2):
            made = make.make(_spec(form, data, {"channels": channels}))
            verdict = check.check(made.bytes, made.media_type, made.spec)
            assert verdict.ok, (form, channels, _failed(verdict))
            assert wavcheck.read(made.bytes).channels == channels


def test_same_spec_same_bytes_and_the_sine_is_exact():
    value = _spec("sound.sonify", {"values": SERIES})
    assert make.make(value).bytes == make.make(value).bytes
    t = np.linspace(-3.7, 5.2, 100_001)
    assert np.max(np.abs(sound.sin_turns(t) - np.sin(2 * np.pi * t))) < 1e-13


def test_reader_reads_a_hand_built_tone_and_its_pitch():
    samples = np.round(0.5 * 32767 * np.sin(2 * np.pi * 440.0 * np.arange(RATE // 2) / RATE)).astype(np.int16)
    w = wavcheck.read(_hand_wav(samples))
    assert (w.rate, w.channels, w.bits, w.format_tag) == (RATE, 1, 16, 1)
    assert len(w.samples) == RATE // 2
    assert abs(wavcheck.pitch(w.samples[:, 0], RATE) - 440.0) < 0.5


def test_hand_built_contract_file_passes_and_a_wrong_pitch_is_caught():
    spec = Spec.from_dict(_spec("sound.sonify", {"values": SERIES}))
    lead = (220.0, 880.0)
    right = _hand_tones([_contract_pitch(v) for v in SERIES], lead=lead)
    assert check.check(_hand_wav(right), "audio/wav", spec).ok
    wrong = [_contract_pitch(v) for v in SERIES]
    wrong[3] += 40.0                                   # one tone 40 Hz sharp: 6 % of the span
    verdict = check.check(_hand_wav(_hand_tones(wrong, lead=lead)), "audio/wav", spec)
    assert not verdict.ok and "values_recovered" in _failed(verdict)
    rec = next(f for f in verdict.technical if f.rule == "values_recovered")
    assert rec.measure == pytest.approx(6 / 7)


def test_swapped_tones_break_the_order():
    spec = Spec.from_dict(_spec("sound.sonify", {"values": [1, 4, 7, 10]}))
    freqs = [_contract_pitch(v) for v in (1, 7, 4, 10)]
    verdict = check.check(_hand_wav(_hand_tones(freqs, lead=(220.0, 880.0))), "audio/wav", spec)
    assert {"values_recovered", "order_preserved"} <= _failed(verdict)


def test_clipped_file_is_caught():
    made = make.make(_spec("sound.sonify", {"values": SERIES}))
    w = wavcheck.read(made.bytes)
    loud = np.clip(w.samples[:, 0] * 3.0, -32768, 32767)
    verdict = check.check(_hand_wav(loud), "audio/wav", made.spec)
    assert "no_clipping" in _failed(verdict)


def test_truncated_data_chunk_is_caught():
    made = make.make(_spec("sound.sonify", {"values": SERIES}))
    cut = made.bytes[: len(made.bytes) // 2]
    verdict = check.check(cut, "audio/wav", made.spec)
    assert {"data_complete", "duration"} <= _failed(verdict)
    # the same with a data chunk that declares more than the file holds but nothing else wrong
    samples = np.zeros(1000, dtype=np.int16)
    verdict = check.check(_hand_wav(samples, declared=4000), "audio/wav")
    assert "data_complete" in _failed(verdict)


def test_clicks_and_dc_offset_are_caught():
    abrupt = _hand_tones([440.0, 523.0], ramp_s=0.0)
    verdict = check.check(_hand_wav(abrupt), "audio/wav")
    assert "no_clicks" in _failed(verdict)
    smooth = _hand_tones([440.0, 523.0])
    assert "no_clicks" not in _failed(check.check(_hand_wav(smooth), "audio/wav"))
    shifted = smooth.astype(np.int32) + 1000
    assert "dc_offset" in _failed(check.check(_hand_wav(shifted), "audio/wav"))


def test_non_pcm16_and_hostile_bytes_are_named_findings():
    floats = _hand_wav(np.zeros(100, dtype=np.float32), fmt_tag=3, bits=32)
    assert "pcm16" in _failed(check.check(floats, "audio/wav"))
    for bad in (b"", b"RIFF", b"RIFF\x00\x00\x00\x00WAVX", b"\x00" * 64, b"RIFF\xff\xff\xff\xffWAVEdata\xff\xff\xff\xff",
                b"RIFF\x24\x00\x00\x00WAVEfmt \xff\xff\xff\xff" + b"\x00" * 20):
        verdict = check.check(bad, "audio/wav")
        assert not verdict.ok
        assert "wav_structure" in _failed(verdict)


def test_spec_layout_mismatch_is_caught():
    made = make.make(_spec("sound.tone", {"frequency_hz": 440, "duration_s": 0.5}))
    other = Spec.from_dict(_spec("sound.tone", {"frequency_hz": 440, "duration_s": 0.5}, {"channels": 2}))
    assert "layout_matches_spec" in _failed(check.check(made.bytes, "audio/wav", other))
    wrong = Spec.from_dict(_spec("sound.tone", {"frequency_hz": 660, "duration_s": 0.5}))
    assert "values_recovered" in _failed(check.check(made.bytes, "audio/wav", wrong))


def test_over_the_duration_bound_is_refused_then_repaired_by_the_stated_rule():
    values = [float((i * 37) % 101) for i in range(1000)]
    value = _spec("sound.sonify", {"values": values})
    with pytest.raises(Refused) as e:
        make.make(value)
    assert e.value.rule == "max_duration"
    fixed = _g2.repair(value, e.value)
    assert fixed is not None and fixed["data"] != value["data"]
    assert len(fixed["data"]["values"]) == 269                 # floor((60 - 0.74) / 0.22)
    assert fixed["options"]["range"] == [0.0, 100.0]
    assert "each the mean of about" in fixed["options"]["history"][0]
    assert "Shortened to fit 60 seconds" in fixed["description"]
    made = make.make(fixed)
    verdict = check.check(made.bytes, made.media_type, made.spec)
    assert verdict.ok, _failed(verdict)
    assert sound.bucket_means([1, 2, None, None, 5, 7], 3) == [1.5, None, 6.0]
    assert _g2.repair(fixed, None) is None                       # no problem, no repair
    assert _g2.repair(_spec("sound.tone", {"frequency_hz": 440, "duration_s": 1}), e.value) is None


def test_impossible_specs_are_named_errors():
    with pytest.raises(Malformed):
        make.make(_spec("sound.tone", {"frequency_hz": 5000, "duration_s": 1}, {"rate_hz": 8000}))
    with pytest.raises(Malformed):
        make.make(_spec("sound.sonify", {"values": [1, 2]}, {"low_hz": 900, "high_hz": 300}))
    with pytest.raises(Malformed):
        make.make(_spec("sound.tone", {"frequency_hz": 440, "duration_s": 1}, {"volume": 3}))
    with pytest.raises(Refused):
        make.make(_spec("sound.tone", {"frequency_hz": 440, "duration_s": 500}))


def test_a_constant_series_sounds_at_the_middle_pitch():
    made = make.make(_spec("sound.sonify", {"values": [4, 4, 4]}, {"reference": "none"}))
    assert check.check(made.bytes, made.media_type, made.spec).ok
    w = wavcheck.read(made.bytes)
    a, b = wavcheck.segments(w.samples[:, 0], RATE)[0]
    assert abs(wavcheck.pitch(w.samples[a:b, 0], RATE) - 550.0) < 1.0
