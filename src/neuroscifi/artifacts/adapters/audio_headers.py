"""Compressed sound read for its headers only: MP3, AAC (ADTS), FLAC, Ogg (Vorbis, Opus, FLAC, Speex), M4A.

NO SAMPLE IS DECODED: no MP3, AAC, FLAC, Vorbis or Opus decoder exists on this host. Each adapter states what the
headers truly carry and then raises `HeaderOnly` (an `Unsupported`) with those facts and with `needs` naming the
missing decoder:
- MP3: the first frame header (MPEG version, layer, bitrate, sample rate, channel mode), the Xing/Info or VBRI
  frame count when present (duration exact to a frame), else a duration ESTIMATED from the bitrate (marked so);
  ID3v2 and ID3v1 text frames as the artifact's own words;
- AAC (ADTS): profile, sample rate, channel configuration, frame count by walking the frame headers, duration
  from 1024 samples per frame;
- FLAC: STREAMINFO (rate, channels, bits per sample, total samples, so the duration) and Vorbis comments as words;
- Ogg: the codec from the first packet (Vorbis, Opus, FLAC, Speex, Theora), its identification header, the last
  page's granule position (the duration), comments as words;
- M4A: the ISO base media track facts (codec, channels, rate, duration) and iTunes text tags as words.
"""
from __future__ import annotations

import struct

from ..safe_b2 import HeaderOnly, Reader
from ..types import Decoded, Detected, Limits, Malformed
from . import Adapter

MP3_BITRATES = {  # (version 1?, layer) -> kbit/s by index
    (True, 1): (0, 32, 64, 96, 128, 160, 192, 224, 256, 288, 320, 352, 384, 416, 448),
    (True, 2): (0, 32, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 384),
    (True, 3): (0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320),
    (False, 1): (0, 32, 48, 56, 64, 80, 96, 112, 128, 144, 160, 176, 192, 224, 256),
    (False, 2): (0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160),
}
MP3_RATES = {3: (44100, 48000, 32000), 2: (22050, 24000, 16000), 0: (11025, 12000, 8000)}
ID3_TEXT = {"TIT2": "title", "TPE1": "artist", "TALB": "album", "TYER": "year", "TDRC": "date", "TCON": "genre",
            "TCOM": "composer", "TRCK": "track", "TPE2": "album artist", "TIT3": "subtitle", "TEXT": "lyricist",
            "TCOP": "copyright", "TPUB": "publisher", "TSSE": "encoder"}
AAC_RATES = (96000, 88200, 64000, 48000, 44100, 32000, 24000, 22050, 16000, 12000, 11025, 8000, 7350)
AAC_PROFILES = ("Main", "LC", "SSR", "LTP")
MAX_WALK = 1_000_000


def _syncsafe(b: bytes) -> int:
    return (b[0] << 21) | (b[1] << 14) | (b[2] << 7) | b[3]


def _id3_text(body: bytes) -> str:
    if not body:
        return ""
    enc, raw = body[0], body[1:]
    try:
        if enc == 0:
            text = raw.decode("latin-1")
        elif enc == 1:
            text = raw.decode("utf-16")
        elif enc == 2:
            text = raw.decode("utf-16-be")
        else:
            text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")
    return text.replace("\x00", " / ").strip(" /")


def read_id3v2(data: bytes) -> tuple[int, list[str], dict]:
    """(bytes the tag occupies, words, facts) of an ID3v2 tag at offset 0, else (0, [], {})."""
    if data[:3] != b"ID3" or len(data) < 10:
        return 0, [], {}
    major, flags, size = data[3], data[5], _syncsafe(data[6:10])
    end = min(len(data), 10 + size + (10 if flags & 0x10 else 0))
    words, facts = [], {"id3v2": f"2.{major}"}
    if flags & 0x80:
        return end, words, {**facts, "id3_unsynchronised": True}
    pos = 10
    if flags & 0x40 and pos + 4 <= end:
        ext = _syncsafe(data[pos:pos + 4]) if major == 4 else struct.unpack(">I", data[pos:pos + 4])[0] + 4
        pos += ext
    n = 0
    while pos + 10 <= end and n < 4096:
        fid = data[pos:pos + 4]
        if not fid.strip(b"\x00") or not fid.isalnum():
            break
        fsize = _syncsafe(data[pos + 4:pos + 8]) if major == 4 else struct.unpack(">I", data[pos + 4:pos + 8])[0]
        body = data[pos + 10:pos + 10 + fsize]
        key = fid.decode("latin-1")
        if key in ID3_TEXT:
            text = _id3_text(body)
            if text:
                words.append(f"{ID3_TEXT[key]}: {text}")
        elif key in ("COMM", "USLT") and len(body) > 4:
            enc = body[0]
            rest = body[4:]
            sep = b"\x00\x00" if enc in (1, 2) else b"\x00"
            at = rest.find(sep)
            if at >= 0:
                text = _id3_text(bytes((enc,)) + rest[at + len(sep):])
                if text:
                    words.append(f"{'comment' if key == 'COMM' else 'lyrics'}: {text}")
        elif key == "APIC":
            facts["embedded_picture"] = True
        pos += 10 + fsize
        n += 1
    return end, words, facts


def _mp3_header(h: bytes):
    if len(h) < 4 or h[0] != 0xFF or h[1] & 0xE0 != 0xE0:
        return None
    version = (h[1] >> 3) & 3          # 3 = MPEG-1, 2 = MPEG-2, 0 = MPEG-2.5
    layer = 4 - ((h[1] >> 1) & 3)      # 1, 2, 3
    bi, ri, pad = h[2] >> 4, (h[2] >> 2) & 3, (h[2] >> 1) & 1
    if version == 1 or layer == 4 or bi in (0, 15) or ri == 3:
        return None
    v1 = version == 3
    bitrate = MP3_BITRATES[(v1, layer)] if v1 else MP3_BITRATES[(False, 1 if layer == 1 else 2)]
    rate = MP3_RATES[version][ri]
    kbps = bitrate[bi]
    if layer == 1:
        length = (12 * kbps * 1000 // rate + pad) * 4
        spf = 384
    else:
        spf = 1152 if (layer == 2 or v1) else 576
        length = spf // 8 * kbps * 1000 // rate + pad
    mode = h[3] >> 6
    return {"version": {3: "1", 2: "2", 0: "2.5"}[version], "layer": layer, "bitrate_kbps": kbps,
            "rate_hz": rate, "channels": 1 if mode == 3 else 2,
            "channel_mode": ("stereo", "joint stereo", "dual channel", "mono")[mode], "frame_bytes": length,
            "samples_per_frame": spf}


def read_mp3(data: bytes) -> tuple[dict, list[str], list[str]]:
    start, words, facts = read_id3v2(data)
    pos = start
    while pos < min(len(data), start + 65536) and not _mp3_header(data[pos:pos + 4]):
        pos += 1
    hdr = _mp3_header(data[pos:pos + 4])
    if hdr is None:
        raise Malformed("no MPEG audio frame header was found")
    facts.update({k: v for k, v in hdr.items() if k != "frame_bytes"})
    facts["format"] = "mp3" if hdr["layer"] == 3 else f"mpeg audio layer {hdr['layer']}"
    notes = []
    frame = data[pos:pos + hdr["frame_bytes"]]
    side = (32 if hdr["channels"] == 2 else 17) if hdr["version"] == "1" else (17 if hdr["channels"] == 2 else 9)
    xing = frame[4 + side:4 + side + 4]
    frames = None
    if xing in (b"Xing", b"Info"):
        flags = struct.unpack(">I", frame[8 + side:12 + side])[0] if len(frame) >= 12 + side else 0
        if flags & 1 and len(frame) >= 16 + side:
            frames = struct.unpack(">I", frame[12 + side:16 + side])[0]
            facts["vbr_header"] = xing.decode()
    elif frame[36:40] == b"VBRI" and len(frame) >= 54:
        frames = struct.unpack(">I", frame[50:54])[0]
        facts["vbr_header"] = "VBRI"
    tail = len(data)
    if data[-128:-125] == b"TAG":
        tail -= 128
        tag = data[-128:]
        for key, a, b in (("title", 3, 33), ("artist", 33, 63), ("album", 63, 93), ("year", 93, 97)):
            v = tag[a:b].split(b"\x00", 1)[0].decode("latin-1").strip()
            if v:
                words.append(f"{key}: {v}")
        facts["id3v1"] = True
    if frames is not None:
        facts["frames"] = frames
        facts["duration_s"] = round(frames * hdr["samples_per_frame"] / hdr["rate_hz"], 3)
    else:
        facts["duration_s_estimate"] = round((tail - pos) * 8 / (hdr["bitrate_kbps"] * 1000), 3)
        notes.append("duration estimated from the first frame's bitrate (no Xing/VBRI header): exact only for CBR")
    return facts, words, notes


def read_adts(data: bytes) -> tuple[dict, list[str], list[str]]:
    start, words, facts = read_id3v2(data)
    pos, frames, first = start, 0, None
    while pos + 7 <= len(data) and frames < MAX_WALK:
        h = data[pos:pos + 7]
        if h[0] != 0xFF or h[1] & 0xF6 != 0xF0:
            break
        length = ((h[3] & 3) << 11) | (h[4] << 3) | (h[5] >> 5)
        if length < 7:
            break
        if first is None:
            first = h
        frames += 1
        pos += length
    if first is None:
        raise Malformed("no ADTS frame header was found")
    profile, ri, ch = first[2] >> 6, (first[2] >> 2) & 0xF, ((first[2] & 1) << 2) | (first[3] >> 6)
    if ri >= len(AAC_RATES):
        raise Malformed("the ADTS header names an invalid sample rate")
    rate = AAC_RATES[ri]
    facts.update({"format": "aac", "container": "ADTS", "profile": AAC_PROFILES[profile], "rate_hz": rate,
                  "channels": ch or "defined in the stream", "frames": frames,
                  "duration_s": round(frames * 1024 / rate, 3)})
    notes = [] if pos >= len(data) else [f"frame walk stopped at byte {pos} of {len(data)}"]
    return facts, words, notes


def _vorbis_comments(body: bytes) -> list[str]:
    r = Reader(body, "<", 0, "a comment block")
    n = r.u32()
    vendor = r.take(n).decode("utf-8", "replace")
    words = [f"encoder: {vendor}"] if vendor else []
    for _ in range(min(r.u32(), 4096)):
        n = r.u32()
        words.append(r.take(n).decode("utf-8", "replace").replace("=", ": ", 1))
    return words


def read_flac(data: bytes, start: int = 4) -> tuple[dict, list[str], list[str]]:
    r = Reader(data, ">", start, "the FLAC metadata")
    facts, words, notes = {"format": "flac"}, [], []
    while r.remaining >= 4:
        head = r.u8()
        size = int.from_bytes(r.take(3), "big")
        body = r.take(size)
        kind = head & 0x7F
        if kind == 0 and size >= 18:
            b = int.from_bytes(body[10:18], "big")
            rate, ch, bps, total = b >> 44, ((b >> 41) & 7) + 1, ((b >> 36) & 31) + 1, b & ((1 << 36) - 1)
            facts.update({"rate_hz": rate, "channels": ch, "bits_per_sample": bps, "total_samples": total})
            if rate and total:
                facts["duration_s"] = round(total / rate, 3)
        elif kind == 4:
            try:
                words += _vorbis_comments(body)
            except Malformed:
                notes.append("the Vorbis comment block is malformed")
        elif kind == 6:
            facts["embedded_picture"] = True
        if head & 0x80:
            break
    if "rate_hz" not in facts:
        raise Malformed("the FLAC STREAMINFO block is missing")
    return facts, words, notes


def _ogg_pages(data: bytes, start: int = 0):
    pos = data.find(b"OggS", start)
    n = 0
    while 0 <= pos and pos + 27 <= len(data) and n < MAX_WALK:
        nseg = data[pos + 26]
        table = data[pos + 27:pos + 27 + nseg]
        size = sum(table)
        granule, serial = struct.unpack("<qI", data[pos + 6:pos + 18])
        body = data[pos + 27 + nseg:pos + 27 + nseg + size]
        yield pos, granule, serial, table, body
        n += 1
        pos = pos + 27 + nseg + size
        if data[pos:pos + 4] != b"OggS":
            break


def _packets(pages, limit=3, max_bytes=1 << 20):
    out, cur = [], b""
    for _, _, _, table, body in pages:
        at = 0
        for seg in table:
            cur += body[at:at + seg]
            at += seg
            if len(cur) > max_bytes:
                return out
            if seg < 255:
                out.append(cur)
                cur = b""
                if len(out) >= limit:
                    return out
    return out


def read_ogg(data: bytes) -> tuple[dict, list[str], list[str]]:
    pages = list(p for _, p in zip(range(64), _ogg_pages(data)))
    if not pages:
        raise Malformed("no Ogg page was found")
    serial = pages[0][2]
    packets = _packets([p for p in pages if p[2] == serial])
    if not packets:
        raise Malformed("the first Ogg packet is missing")
    first = packets[0]
    facts, words, notes = {}, [], []
    rate, pre_skip = None, 0
    if first[:7] == b"\x01vorbis" and len(first) >= 30:
        _, ch, rate, bmax, bnom, bmin = struct.unpack("<IBIiii", first[7:28])
        facts.update({"format": "ogg vorbis", "channels": ch, "rate_hz": rate})
        if bnom > 0:
            facts["nominal_bitrate"] = bnom
        if len(packets) > 1 and packets[1][:7] == b"\x03vorbis":
            words += _vorbis_comments(packets[1][7:])
    elif first[:8] == b"OpusHead" and len(first) >= 19:
        _, ch, pre_skip, in_rate = struct.unpack("<BBHI", first[8:16])
        rate = 48000
        facts.update({"format": "ogg opus", "channels": ch, "rate_hz": 48000, "input_rate_hz": in_rate})
        if len(packets) > 1 and packets[1][:8] == b"OpusTags":
            words += _vorbis_comments(packets[1][8:])
    elif first[:5] == b"\x7fFLAC":
        f, w, n = read_flac(first, 13)
        f["format"] = "ogg flac"
        facts.update(f)
        words += w
        notes += n
        rate = f.get("rate_hz")
    elif first[:8] == b"Speex   " and len(first) >= 48:
        rate, = struct.unpack("<I", first[36:40])
        facts.update({"format": "ogg speex", "rate_hz": rate, "channels": struct.unpack("<I", first[48:52])[0]})
    elif first[:7] == b"\x80theora":
        facts["format"] = "ogg theora (video)"
    else:
        facts["format"] = "ogg (unknown codec)"
    serials = {p[2] for p in pages}
    if len(serials) > 1:
        facts["logical_streams_in_first_pages"] = len(serials)
    last = None
    tail_at = max(0, len(data) - 65536)
    for page in _ogg_pages(data, tail_at):
        if page[2] == serial and page[1] >= 0:
            last = page[1]
    if last is not None and rate:
        facts["duration_s"] = round(max(0, last - pre_skip) / rate, 3)
    elif rate:
        notes.append("the last page's granule position could not be read: duration unknown")
    return facts, words, notes


def _raise(fmt: str, facts: dict, words: list[str], notes: list[str], needs: str, size: int):
    desc = facts.get("format", fmt)
    bits = [f"{facts[k]} {u}" for k, u in (("channels", "channel(s)"), ("rate_hz", "Hz"),
                                            ("duration_s", "s")) if k in facts]
    message = f"{desc}" + (f" ({', '.join(bits)})" if bits else "") + ": samples are not decoded here (no " \
        f"{needs} on this host)"
    if notes:
        facts = {**facts, "notes": "; ".join(notes)}
    raise HeaderOnly(message, facts=facts, needs=(needs,), size=size, words="\n".join(words))


class MP3Adapter(Adapter):
    name = "b2-mp3"
    formats = ("mp3",)
    families = ("audio",)

    def decode(self, data, detected, limits):
        facts, words, notes = read_mp3(data)
        _raise("mp3", facts, words, notes, "MP3 decoder", len(data))


class AACAdapter(Adapter):
    name = "b2-aac"
    formats = ("aac",)
    families = ("audio",)

    def decode(self, data, detected, limits):
        facts, words, notes = read_adts(data)
        _raise("aac", facts, words, notes, "AAC decoder", len(data))


class FLACAdapter(Adapter):
    name = "b2-flac"
    formats = ("flac",)
    families = ("audio",)

    def decode(self, data, detected, limits):
        if data[:4] != b"fLaC":
            raise Malformed("the FLAC magic is missing")
        facts, words, notes = read_flac(data)
        _raise("flac", facts, words, notes, "FLAC decoder", len(data))


class OggAdapter(Adapter):
    name = "b2-ogg"
    formats = ("ogg", "opus", "vorbis", "ogv")
    families = ("audio",)

    def decode(self, data, detected, limits):
        facts, words, notes = read_ogg(data)
        codec = facts["format"].replace("ogg ", "").split(" ")[0]
        need = {"vorbis": "Vorbis decoder", "opus": "Opus decoder", "flac": "FLAC decoder",
                "speex": "Speex decoder", "theora": "Theora video decoder"}.get(codec, "decoder for this Ogg codec")
        _raise("ogg", facts, words, notes, need, len(data))


class M4AAdapter(Adapter):
    name = "b2-m4a"
    formats = ("m4a",)
    families = ("audio",)

    def decode(self, data, detected: Detected, limits: Limits) -> Decoded:
        from .video_containers import read_iso
        info = read_iso(data, limits)
        audio = [t for t in info["tracks"] if t.get("kind") == "audio"]
        facts = {"format": "m4a", **info["facts"]}
        if audio:
            t = audio[0]
            facts.update({k: t[k] for k in ("codec", "channels", "sample_rate_hz", "duration_s") if k in t})
            if "sample_rate_hz" in facts:
                facts["rate_hz"] = facts.pop("sample_rate_hz")
        codec = facts.get("codec", "")
        need = {"mp4a": "AAC decoder", "alac": "ALAC decoder", "Opus": "Opus decoder",
                "fLaC": "FLAC decoder", "ac-3": "AC-3 decoder"}.get(codec, f"{codec or 'audio'} decoder")
        _raise("m4a", facts, info["words"], info["notes"], need, len(data))


ADAPTERS = (MP3Adapter(), AACAdapter(), FLACAdapter(), OggAdapter(), M4AAdapter())

__all__ = ["AACAdapter", "ADAPTERS", "FLACAdapter", "M4AAdapter", "MP3Adapter", "OggAdapter", "read_adts",
           "read_flac", "read_id3v2", "read_mp3", "read_ogg"]
