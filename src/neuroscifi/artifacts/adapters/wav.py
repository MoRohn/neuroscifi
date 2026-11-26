"""Uncompressed sound decoded to samples: WAV/RIFF, AIFF/AIFC, Sun/NeXT AU.

WAV: PCM 8/16/24/32-bit, IEEE float 32/64, WAVE_FORMAT_EXTENSIBLE (PCM or float sub-format, channel mask), G.711
A-law and mu-law, any channel count, chunks in any order with odd-size padding; `LIST`/`INFO` text fields are the
artifact's own words. AIFF/AIFC: big-endian PCM 8-32 bit ('NONE', 'twos'), little-endian 'sowt', float 'fl32'/'fl64',
'ulaw'/'alaw'; NAME/AUTH/(c)/ANNO text. AU: linear 8-32 bit, float, double, mu-law, A-law; the annotation is words.

A declared data size larger than what is present (a lie, or a stream header) reads what is there: the result is
marked partial and the notes say how much was declared. Samples are float32 in [-1, 1]: integers are divided by
2^(bits-1); float samples outside [-1, 1] are clipped and non-finite ones set to 0, and the notes count them.
Compressed encodings (ADPCM, MPEG-in-WAV, ...) are identified and refused with the header facts (`HeaderOnly`).
"""
from __future__ import annotations

import math
import struct

import numpy as np

from .. import repr_b2 as R
from ..safe_b2 import Deadline, HeaderOnly, Reader, members, need, screen_words
from ..types import Decoded, Detected, Limits, Malformed
from . import Adapter

WAV_TAGS = {1: "pcm", 3: "float", 6: "alaw", 7: "mulaw", 0xFFFE: "extensible"}
KNOWN_COMPRESSED = {2: "Microsoft ADPCM", 0x11: "IMA ADPCM", 0x31: "GSM 6.10", 0x50: "MPEG audio",
                    0x55: "MPEG layer III", 0xFF: "AAC", 0x161: "Windows Media Audio", 0x2000: "AC-3",
                    0xF1AC: "FLAC"}
SPEAKERS = ("front_left", "front_right", "front_center", "low_frequency", "back_left", "back_right",
            "front_left_of_center", "front_right_of_center", "back_center", "side_left", "side_right", "top_center",
            "top_front_left", "top_front_center", "top_front_right", "top_back_left", "top_back_center",
            "top_back_right")
INFO_FIELDS = {b"INAM": "title", b"IART": "artist", b"ICMT": "comment", b"ICOP": "copyright", b"ICRD": "date",
               b"IGNR": "genre", b"ISFT": "software", b"IPRD": "album", b"IKEY": "keywords", b"ISBJ": "subject",
               b"IENG": "engineer", b"ITCH": "technician", b"ISRC": "source"}
MAX_CHANNELS = 256


def _ulaw_table() -> np.ndarray:
    u = ~np.arange(256, dtype=np.int32) & 0xFF
    sign, exponent, mantissa = u & 0x80, (u >> 4) & 0x07, u & 0x0F
    magnitude = (((mantissa << 3) + 0x84) << exponent) - 0x84
    return (np.where(sign, -magnitude, magnitude) / 32768.0).astype(np.float32)


def _alaw_table() -> np.ndarray:
    a = np.arange(256, dtype=np.int32) ^ 0x55
    sign, exponent, mantissa = a & 0x80, (a >> 4) & 0x07, a & 0x0F
    magnitude = np.where(exponent == 0, (mantissa << 4) + 8, ((mantissa << 4) + 0x108) << np.maximum(exponent - 1, 0))
    return (np.where(sign, magnitude, -magnitude) / 32768.0).astype(np.float32)


ULAW, ALAW = _ulaw_table(), _alaw_table()


def default_names(n: int) -> tuple[str, ...]:
    if n == 1:
        return ("mono",)
    if n == 2:
        return ("left", "right")
    return tuple(f"channel {i + 1}" for i in range(n))


def to_float(raw: bytes, *, encoding: str, bits: int, channels: int, endian: str, notes: list) -> np.ndarray:
    """Interleaved sample bytes -> float32 (n, channels) in [-1, 1]."""
    width = {"pcm": (bits + 7) // 8, "float": bits // 8, "mulaw": 1, "alaw": 1, "pcm_u8": 1}[encoding]
    frame = width * channels
    n = len(raw) // frame
    raw = raw[:n * frame]
    if encoding in ("mulaw", "alaw"):
        table = ULAW if encoding == "mulaw" else ALAW
        out = table[np.frombuffer(raw, np.uint8)]
    elif encoding == "pcm_u8":
        out = (np.frombuffer(raw, np.uint8).astype(np.float32) - 128.0) / 128.0
    elif encoding == "float":
        vals = np.frombuffer(raw, np.dtype(f"{endian}f{width}")).astype(np.float64)
        bad = ~np.isfinite(vals)
        if bad.any():
            notes.append(f"{int(bad.sum())} non-finite float sample(s) were set to 0")
            vals = np.where(bad, 0.0, vals)
        over = np.abs(vals) > 1.0
        if over.any():
            notes.append(f"{int(over.sum())} float sample(s) outside [-1, 1] were clipped")
            vals = np.clip(vals, -1.0, 1.0)
        out = vals.astype(np.float32)
    elif width == 3:
        b = np.frombuffer(raw, np.uint8).reshape(-1, 3).astype(np.int32)
        if endian == "<":
            v = b[:, 0] | (b[:, 1] << 8) | (b[:, 2] << 16)
        else:
            v = b[:, 2] | (b[:, 1] << 8) | (b[:, 0] << 16)
        v = np.where(v & 0x800000, v - (1 << 24), v)
        out = (v / float(1 << 23)).astype(np.float32)
    else:
        v = np.frombuffer(raw, np.dtype(f"{endian}i{width}")).astype(np.float64)
        out = (v / float(1 << (8 * width - 1))).astype(np.float32)
    out = np.clip(out, -1.0, 1.0)      # -32768/32768 is exactly -1; nothing exceeds 1 except float handled above
    return out.reshape(n, channels)


def _budget(n_bytes: int, width: int, channels: int, limits: Limits) -> None:
    frames = n_bytes // max(1, width * channels)
    need(n_bytes + frames * channels * 4 * 2, limits, "the decoded samples")


def _facts(fmt, **kw) -> dict:
    return {"format": fmt, **{k: v for k, v in kw.items() if v is not None}}


# ---------------------------------------------------------------------------------------------------------------- WAV


def _decode_wav(data: bytes, detected: Detected, limits: Limits) -> Decoded:
    deadline = Deadline(limits)
    if data[:4] == b"RIFX":
        endian = ">"
    elif data[:4] == b"RIFF":
        endian = "<"
    else:
        raise Malformed("the RIFF header is missing")
    if data[8:12] != b"WAVE":
        raise Malformed("the RIFF form type is not WAVE")
    notes, words = [], []
    declared = struct.unpack(endian + "I", data[4:8])[0]
    if declared + 8 != len(data):
        notes.append(f"the RIFF header declares {declared + 8} bytes, the file holds {len(data)}")
    r = Reader(data, endian, 12, "the WAV file")
    fmt = None
    sample_data = None
    partial = False
    n_chunks = 0
    while r.remaining >= 8:
        cid = r.take(4)
        size = r.u32()
        n_chunks += 1
        members(n_chunks, limits, "WAV chunks")
        if size > r.remaining:
            if cid != b"data":
                notes.append(f"the last chunk ({cid.decode('latin-1')!r}) is cut short and was skipped")
                break
            notes.append(f"the data chunk declares {size} bytes but only {r.remaining} are present")
            size, partial = r.remaining, True
        body = r.take(size)
        if size % 2 and r.remaining:
            r.skip(1)
        if cid == b"fmt ":
            fmt = body
        elif cid == b"data":
            if sample_data is None:
                sample_data = body
            else:
                notes.append("a second data chunk was ignored")
        elif cid == b"LIST" and body[:4] == b"INFO":
            lr = Reader(body, endian, 4, "a LIST chunk")
            while lr.remaining >= 8:
                sid = lr.take(4)
                ssize = lr.u32()
                if ssize > lr.remaining:
                    break
                value = lr.take(ssize).split(b"\x00", 1)[0].decode("latin-1").strip()
                if ssize % 2 and lr.remaining:
                    lr.skip(1)
                if value:
                    words.append(f"{INFO_FIELDS.get(sid, sid.decode('latin-1'))}: {value}")
        deadline.check("reading WAV chunks")
    if fmt is None or len(fmt) < 16:
        raise Malformed("the WAV file has no valid fmt chunk")
    tag, channels, rate, _byte_rate, block, bits = struct.unpack(endian + "HHIIHH", fmt[:16])
    mask = None
    if tag == 0xFFFE:
        if len(fmt) < 40:
            raise Malformed("an extensible WAV fmt chunk is too short")
        valid_bits, mask = struct.unpack(endian + "HI", fmt[18:24])
        tag = struct.unpack(endian + "H", fmt[24:26])[0]
        if valid_bits and valid_bits < bits:
            notes.append(f"{valid_bits} valid bits in {bits}-bit containers: decoded at the container width")
    facts = _facts("wav", channels=channels, rate_hz=rate, bits=bits, encoding=WAV_TAGS.get(tag) or
                   KNOWN_COMPRESSED.get(tag, f"format tag {tag:#06x}"),
                   data_bytes=len(sample_data) if sample_data is not None else None)
    if not 1 <= channels <= MAX_CHANNELS or not 1 <= rate <= 10_000_000:
        raise Malformed("the WAV fmt chunk declares an impossible channel count or sample rate")
    if tag == 1:
        if bits not in (8, 16, 24, 32):
            raise HeaderOnly(f"{bits}-bit PCM WAV is not decoded here", facts=facts,
                             needs=(f"a decoder for {bits}-bit PCM",), size=len(data))
        encoding = "pcm_u8" if bits == 8 else "pcm"
    elif tag == 3:
        if bits not in (32, 64):
            raise Malformed("an IEEE float WAV declares a width other than 32 or 64 bits")
        encoding = "float"
    elif tag in (6, 7):
        encoding, bits = ("alaw" if tag == 6 else "mulaw"), 8
        notes.append("G.711 " + ("A-law" if tag == 6 else "mu-law") + " samples expanded to linear")
    else:
        name = KNOWN_COMPRESSED.get(tag, f"format tag {tag:#06x}")
        raise HeaderOnly(f"WAV holding {name}: {channels} channel(s) at {rate} Hz; samples are not decoded here",
                         facts=facts, needs=(f"a {name} decoder",), size=len(data))
    if sample_data is None:
        raise Malformed("the WAV file has no data chunk")
    width = 1 if encoding in ("pcm_u8", "alaw", "mulaw") else bits // 8
    if block and block != width * channels:
        notes.append(f"the block alignment {block} disagrees with {channels} x {width} bytes; the latter was used")
    _budget(len(sample_data), width, channels, limits)
    if len(sample_data) % (width * channels):
        notes.append("the data chunk ends inside a frame; the incomplete frame was dropped")
    samples = to_float(sample_data, encoding=encoding, bits=bits, channels=channels, endian=endian, notes=notes)
    names = default_names(channels)
    if mask:
        named = [SPEAKERS[i] for i in range(len(SPEAKERS)) if mask >> i & 1]
        if len(named) == channels:
            names = tuple(named)
    text = "\n".join(words)
    notes.extend(screen_words(text))
    return Decoded(detected, R.Signal(samples, float(rate), names, words=text), tuple(notes), partial)


# --------------------------------------------------------------------------------------------------------------- AIFF


def extended80(b: bytes) -> float:
    """An IEEE 754 80-bit extended float (big-endian), as AIFF stores its sample rate."""
    exp, mant = struct.unpack(">HQ", b)
    sign = -1.0 if exp & 0x8000 else 1.0
    exp &= 0x7FFF
    if exp == 0 and mant == 0:
        return 0.0
    if exp == 0x7FFF:
        return math.inf
    return sign * mant * 2.0 ** (exp - 16383 - 63)


AIFC_CODES = {b"NONE": ("pcm", ">"), b"twos": ("pcm", ">"), b"sowt": ("pcm", "<"), b"fl32": ("float", ">"),
              b"FL32": ("float", ">"), b"fl64": ("float", ">"), b"FL64": ("float", ">"), b"ulaw": ("mulaw", ">"),
              b"ULAW": ("mulaw", ">"), b"alaw": ("alaw", ">"), b"ALAW": ("alaw", ">"), b"raw ": ("pcm_u8", ">")}
AIFF_TEXT = {b"NAME": "title", b"AUTH": "author", b"(c) ": "copyright", b"ANNO": "annotation"}


def _decode_aiff(data: bytes, detected: Detected, limits: Limits) -> Decoded:
    deadline = Deadline(limits)
    if data[:4] != b"FORM" or data[8:12] not in (b"AIFF", b"AIFC"):
        raise Malformed("the IFF header is not AIFF or AIFC")
    aifc = data[8:12] == b"AIFC"
    notes, words = [], []
    r = Reader(data, ">", 12, "the AIFF file")
    comm = ssnd = None
    partial = False
    n_chunks = 0
    while r.remaining >= 8:
        cid = r.take(4)
        size = r.u32()
        n_chunks += 1
        members(n_chunks, limits, "AIFF chunks")
        if size > r.remaining:
            if cid != b"SSND":
                break
            notes.append(f"the SSND chunk declares {size} bytes but only {r.remaining} are present")
            size, partial = r.remaining, True
        body = r.take(size)
        if size % 2 and r.remaining:
            r.skip(1)
        if cid == b"COMM":
            comm = body
        elif cid == b"SSND":
            ssnd = body
        elif cid in AIFF_TEXT:
            value = body.split(b"\x00", 1)[0].decode("latin-1").strip()
            if value:
                words.append(f"{AIFF_TEXT[cid]}: {value}")
        deadline.check("reading AIFF chunks")
    if comm is None or len(comm) < 18:
        raise Malformed("the AIFF file has no valid COMM chunk")
    channels, n_frames, bits = struct.unpack(">hIh", comm[:8])
    rate = extended80(comm[8:18])
    code = comm[18:22] if aifc and len(comm) >= 22 else b"NONE"
    facts = _facts("aiff", channels=channels, frames=n_frames, rate_hz=rate, bits=bits,
                   compression=code.decode("latin-1"))
    if not (1 <= channels <= MAX_CHANNELS) or not (math.isfinite(rate) and 0 < rate <= 10_000_000):
        raise Malformed("the AIFF COMM chunk declares an impossible channel count or sample rate")
    if code not in AIFC_CODES:
        raise HeaderOnly(f"AIFC compressed with {code.decode('latin-1')!r} is not decoded here", facts=facts,
                         needs=(f"a decoder for AIFC compression {code.decode('latin-1')!r}",), size=len(data))
    encoding, endian = AIFC_CODES[code]
    if encoding == "pcm" and not 1 <= bits <= 32:
        raise Malformed("the AIFF COMM chunk declares an impossible sample width")
    if encoding == "float":
        bits = 64 if code.lower() == b"fl64" else 32
    if ssnd is None or len(ssnd) < 8:
        raise Malformed("the AIFF file has no SSND chunk")
    offset = struct.unpack(">I", ssnd[:4])[0]
    raw = ssnd[8 + offset:]
    width = {"pcm": (bits + 7) // 8, "float": bits // 8}.get(encoding, 1)
    want = n_frames * width * channels
    if len(raw) < want:
        notes.append(f"COMM declares {n_frames} frames; the SSND chunk holds {len(raw) // (width * channels)}")
        partial = True
    raw = raw[:want]
    _budget(len(raw), width, channels, limits)
    if encoding == "pcm" and width == 1:
        samples = (np.frombuffer(raw[:len(raw) // channels * channels], np.int8).astype(np.float32) / 128.0)
        samples = samples.reshape(-1, channels)
    else:
        samples = to_float(raw, encoding=encoding, bits=width * 8 if encoding == "pcm" else bits,
                           channels=channels, endian=endian, notes=notes)
    if encoding == "pcm" and bits % 8:
        notes.append(f"{bits}-bit samples stored in {width * 8}-bit containers: scaled at the container width")
    text = "\n".join(words)
    notes.extend(screen_words(text))
    return Decoded(detected, R.Signal(samples, float(rate), default_names(channels), words=text), tuple(notes),
                   partial)


# ----------------------------------------------------------------------------------------------------------------- AU

AU_ENCODINGS = {1: ("mulaw", 8), 2: ("pcm_s8", 8), 3: ("pcm", 16), 4: ("pcm", 24), 5: ("pcm", 32), 6: ("float", 32),
                7: ("float", 64), 27: ("alaw", 8)}


def _decode_au(data: bytes, detected: Detected, limits: Limits) -> Decoded:
    r = Reader(data, ">", 0, "the AU file")
    if r.take(4) != b".snd":
        raise Malformed("the AU magic is missing")
    offset, size, enc, rate, channels = r.unpack("IIIII")
    facts = _facts("au", channels=channels, rate_hz=rate, encoding=enc)
    if offset < 24 or offset > len(data) or not 1 <= channels <= MAX_CHANNELS or not 1 <= rate <= 10_000_000:
        raise Malformed("the AU header declares an impossible offset, channel count or sample rate")
    notes = []
    words = data[24:offset].split(b"\x00", 1)[0].decode("latin-1").strip()
    if enc not in AU_ENCODINGS:
        raise HeaderOnly(f"AU encoding {enc} is not decoded here", facts=facts,
                         needs=(f"a decoder for AU encoding {enc}",), size=len(data))
    encoding, bits = AU_ENCODINGS[enc]
    raw = data[offset:]
    partial = False
    if size != 0xFFFFFFFF:
        if size > len(raw):
            notes.append(f"the header declares {size} data bytes, {len(raw)} are present")
            partial = True
        raw = raw[:size]
    width = bits // 8
    _budget(len(raw), width, channels, limits)
    if encoding == "pcm_s8":
        n = len(raw) // channels * channels
        samples = (np.frombuffer(raw[:n], np.int8).astype(np.float32) / 128.0).reshape(-1, channels)
    else:
        samples = to_float(raw, encoding=encoding, bits=bits, channels=channels, endian=">", notes=notes)
    notes.extend(screen_words(words))
    return Decoded(detected, R.Signal(samples, float(rate), default_names(channels), words=words), tuple(notes),
                   partial)


class WAVAdapter(Adapter):
    name = "b2-wav"
    formats = ("wav",)
    families = ("audio",)

    def decode(self, data, detected, limits):
        return _decode_wav(data, detected, limits)


class AIFFAdapter(Adapter):
    name = "b2-aiff"
    formats = ("aiff",)
    families = ("audio",)

    def decode(self, data, detected, limits):
        return _decode_aiff(data, detected, limits)


class AUAdapter(Adapter):
    name = "b2-au"
    formats = ("au",)
    families = ("audio",)

    def decode(self, data, detected, limits):
        return _decode_au(data, detected, limits)


ADAPTERS = (WAVAdapter(), AIFFAdapter(), AUAdapter())

__all__ = ["ADAPTERS", "AIFFAdapter", "AUAdapter", "WAVAdapter", "default_names", "extended80", "to_float"]
