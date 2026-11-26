"""WAV, AIFF/AIFC and AU (lane B2): samples recovered from files built byte by byte from a reference; size lies,
odd chunk order and hostile headers; and the signal measurements on hand-made signals."""
import math
import struct

import numpy as np
import pytest

from b2_fixtures import fmt_chunk, riff, b2_registered  # noqa: F401
from neuroscifi.artifacts import adapters, measure_audio
from neuroscifi.artifacts import types as T
from neuroscifi.artifacts.adapters import _b2
from neuroscifi.artifacts.safe_b2 import HeaderOnly


REF = [0, 1000, -1000, 32767, -32768, 12345]           # 16-bit reference samples, interleaved stereo


def _pcm16(values):
    return b"".join(struct.pack("<h", v) for v in values)


def test_wav_pcm16_stereo_values_and_info_words():
    info = b"INFO" + b"INAM" + struct.pack("<I", 6) + b"Hello\x00" + b"IART" + struct.pack("<I", 3) + b"Ada\x00"
    data = riff(b"WAVE", [(b"LIST", info), (b"data", _pcm16(REF)), (b"fmt ", fmt_chunk(1, 2, 8000, 16))])
    out = adapters.decode(data)
    sig = out.representation
    assert isinstance(sig, T.Signal) and sig.rate_hz == 8000 and sig.channel_names == ("left", "right")
    expect = (np.array(REF, dtype=np.float64) / 32768).reshape(3, 2)
    assert np.allclose(sig.samples, expect, atol=1e-7)
    assert "title: Hello" in sig.text() and "artist: Ada" in sig.text()
    assert out.detected.format == "wav" and not out.partial


@pytest.mark.parametrize("bits", [8, 24, 32])
def test_wav_pcm_widths(bits):
    vals = [0, 1, -1, 100, -100]
    if bits == 8:
        raw = bytes(v + 128 for v in vals)
        expect = np.array(vals) / 128
    elif bits == 24:
        raw = b"".join(struct.pack("<i", v)[:3] for v in vals)
        expect = np.array(vals) / (1 << 23)
    else:
        raw = b"".join(struct.pack("<i", v * 65536) for v in vals)
        expect = np.array(vals) * 65536 / (1 << 31)
    sig = adapters.decode(riff(b"WAVE", [(b"fmt ", fmt_chunk(1, 1, 16000, bits)), (b"data", raw)])).representation
    assert np.allclose(sig.samples[:, 0], expect, atol=1e-7)


def test_wav_float_and_extensible_with_channel_mask():
    vals = [0.5, -0.25, 1.5, float("nan")]
    raw = struct.pack("<4f", *vals)
    fmt = fmt_chunk(0xFFFE, 2, 44100, 32, extensible_subtype=3)
    fmt = fmt[:20] + struct.pack("<I", 0x3) + fmt[24:]          # mask: front left + front right
    out = adapters.decode(riff(b"WAVE", [(b"fmt ", fmt), (b"data", raw)]))
    sig = out.representation
    assert sig.channel_names == ("front_left", "front_right")
    assert sig.samples.tolist() == [[0.5, -0.25], [1.0, 0.0]]
    assert any("clipped" in n for n in out.notes) and any("non-finite" in n for n in out.notes)
    raw64 = struct.pack("<2d", 0.125, -0.75)
    sig = adapters.decode(riff(b"WAVE", [(b"fmt ", fmt_chunk(3, 1, 8000, 64)), (b"data", raw64)])).representation
    assert sig.samples[:, 0].tolist() == [0.125, -0.75]


def test_wav_g711_mulaw_and_alaw_reference_points():
    # mu-law 0xFF and A-law 0xD5 are the codes for (near) zero; mu-law 0x80 is the largest positive value 32124
    sig = adapters.decode(riff(b"WAVE", [(b"fmt ", fmt_chunk(7, 1, 8000, 8)), (b"data", b"\xff\x80\x00")]))
    s = sig.representation.samples[:, 0] * 32768
    assert s.tolist() == [0.0, 32124.0, -32124.0]
    sig = adapters.decode(riff(b"WAVE", [(b"fmt ", fmt_chunk(6, 1, 8000, 8)), (b"data", b"\xd5\x55\xaa")]))
    s = sig.representation.samples[:, 0] * 32768
    assert s.tolist() == [8.0, -8.0, 32256.0]


def test_wav_data_size_lie_is_bounded_and_partial():
    body = riff(b"WAVE", [(b"fmt ", fmt_chunk(1, 1, 8000, 16)), (b"data", _pcm16([1, 2, 3, 4]))])
    lie = body.replace(b"data" + struct.pack("<I", 8), b"data" + struct.pack("<I", 0xFFFFFFF0))
    out = adapters.decode(lie)
    assert out.partial and out.representation.samples.shape == (4, 1)
    assert any("declares" in n for n in out.notes)


def test_wav_compressed_is_header_only():
    fmt = struct.pack("<HHIIHH", 2, 1, 22050, 11025, 512, 4)
    with pytest.raises(HeaderOnly) as err:
        adapters.decode(riff(b"WAVE", [(b"fmt ", fmt), (b"data", b"\x00" * 64)]))
    assert err.value.facts["encoding"] == "Microsoft ADPCM" and err.value.facts["rate_hz"] == 22050
    assert "ADPCM" in err.value.needs[0] and isinstance(err.value.opaque, T.Opaque)


def test_wav_hostile_headers():
    with pytest.raises(T.Malformed):
        adapters.decode(riff(b"WAVE", [(b"data", b"\x00\x00")]))              # no fmt
    with pytest.raises(T.Malformed):
        adapters.decode(riff(b"WAVE", [(b"fmt ", fmt_chunk(1, 0, 8000, 16)), (b"data", b"")]))
    big = riff(b"WAVE", [(b"fmt ", fmt_chunk(1, 2, 8000, 16)), (b"data", b"\x00" * 4000)])
    with pytest.raises(T.Refused):
        adapters.decode(big, limits=T.Limits(max_decoded_bytes=1000))


def _ext80(x):
    """80-bit extended float, big-endian (independent encoder)."""
    if x == 0:
        return bytes(10)
    m, e = math.frexp(x)                 # x = m * 2**e, 0.5 <= m < 1
    mant = int(m * (1 << 64))
    return struct.pack(">HQ", e - 1 + 16383, mant)


def _iff(form, chunks):
    body = form
    for cid, data in chunks:
        body += cid + struct.pack(">I", len(data)) + data + (b"\x00" if len(data) % 2 else b"")
    return b"FORM" + struct.pack(">I", len(body)) + body


def test_aiff_and_aifc_sowt_and_float():
    vals = [0, 16384, -16384, 32767]
    comm = struct.pack(">hIh", 1, 4, 16) + _ext80(44100.0)
    ssnd = struct.pack(">II", 0, 0) + b"".join(struct.pack(">h", v) for v in vals)
    out = adapters.decode(_iff(b"AIFF", [(b"COMM", comm), (b"NAME", b"Tone"), (b"SSND", ssnd)]))
    sig = out.representation
    assert out.detected.format == "aiff" and sig.rate_hz == 44100.0 and "title: Tone" in sig.text()
    assert np.allclose(sig.samples[:, 0], np.array(vals) / 32768)
    comm = struct.pack(">hIh", 1, 4, 16) + _ext80(8000.0) + b"sowt" + b"\x00"
    ssnd = struct.pack(">II", 0, 0) + b"".join(struct.pack("<h", v) for v in vals)
    assert np.allclose(adapters.decode(_iff(b"AIFC", [(b"COMM", comm), (b"SSND", ssnd)])).representation.samples[:, 0],
                       np.array(vals) / 32768)
    comm = struct.pack(">hIh", 1, 2, 32) + _ext80(8000.0) + b"fl32" + b"\x00"
    ssnd = struct.pack(">II", 0, 0) + struct.pack(">2f", 0.25, -0.5)
    assert adapters.decode(_iff(b"AIFC", [(b"COMM", comm), (b"SSND", ssnd)])).representation.samples[:, 0].tolist() \
        == [0.25, -0.5]
    comm = struct.pack(">hIh", 1, 2, 16) + _ext80(8000.0) + b"ima4" + b"\x00"
    with pytest.raises(HeaderOnly):
        adapters.decode(_iff(b"AIFC", [(b"COMM", comm), (b"SSND", struct.pack(">II", 0, 0))]))


def test_au_linear16_and_annotation():
    vals = [0, 32767, -32768]
    hdr = b".snd" + struct.pack(">IIIII", 32, 6, 3, 8000, 1) + b"note\x00\x00\x00\x00"
    out = adapters.decode(hdr + b"".join(struct.pack(">h", v) for v in vals))
    assert out.detected.format == "au" and out.representation.text() == "note"
    assert np.allclose(out.representation.samples[:, 0], np.array(vals) / 32768)


# ------------------------------------------------------------------------------------------------------- measurements


def _sig(x, rate=8000):
    x = np.asarray(x, dtype=np.float32)
    return T.Signal(x[:, None] if x.ndim == 1 else x, float(rate), ("mono",) if x.ndim == 1 else ("l", "r"))


def test_basic_measurements_on_a_known_signal():
    rate = 8000
    t = np.arange(rate) / rate
    x = 0.5 * np.sin(2 * np.pi * 440 * t) + 0.1
    s = _sig(x)
    assert measure_audio.duration(s) == 1.0
    assert abs(measure_audio.dc_offset(s)[0] - 0.1) < 1e-3
    assert abs(measure_audio.peak(s)[0] - 0.6) < 1e-3
    assert abs(measure_audio.rms(s)[0] - math.sqrt(0.125 + 0.01)) < 1e-3
    assert measure_audio.clipping_share(s) == 0.0
    clipped = _sig(np.clip(2 * np.sin(2 * np.pi * 5 * t), -1, 1))
    assert 0.3 < measure_audio.clipping_share(clipped) < 0.9
    pure = _sig(0.5 * np.sin(2 * np.pi * 440 * t))
    assert abs(measure_audio.zero_crossing_rate(pure) - 880) <= 2
    dom = measure_audio.dominant_frequency(pure, segments=2)
    assert all(abs(d["hz"] - 440) <= rate / measure_audio.N_FFT for d in dom)
    assert abs(measure_audio.spectral_centroid(pure) - 440) < 30


def test_silence_onsets_and_tempo():
    rate = 8000
    x = np.zeros(rate * 4)
    clicks = np.arange(0.25, 4.0, 0.5)              # 120 BPM: a short burst every 0.5 s
    rng = np.random.default_rng(0)
    for c in clicks:
        i = int(c * rate)
        x[i:i + 200] = rng.uniform(-0.8, 0.8, 200)
    s = _sig(x)
    found = measure_audio.onsets(s)
    assert len(found) == len(clicks)
    assert all(abs(f - c) < 0.07 for f, c in zip(found, clicks))
    t = measure_audio.tempo(s, found)
    assert t["bpm"] is not None and abs(t["bpm"] - 120) < 3
    quiet = measure_audio.silence_segments(_sig(np.concatenate([0.5 * np.ones(800), np.zeros(4000), 0.5 * np.ones(800)])))
    assert len(quiet) == 1 and abs(quiet[0][0] - 0.1) < 0.03 and abs(quiet[0][1] - 0.6) < 0.03
    few = measure_audio.tempo(s, found[:3])
    assert few["bpm"] is None and "fewer" in few["reason"]


def test_analyse_is_json_able_and_bounded():
    import json
    s = _sig(np.sin(np.arange(16000) / 5.0) * 0.3)
    raw = json.dumps(measure_audio.analyse(s), allow_nan=False)
    assert len(raw) < 32 * 1024


def test_shape_rule_for_signals():
    sh = _b2.content_shape(_sig(np.zeros(16000)))
    assert sh.kinds == ("sound", "series") and sh.duration_s == 2.0 and sh.has_time
