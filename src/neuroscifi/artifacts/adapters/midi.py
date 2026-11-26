"""Standard MIDI Files (formats 0, 1 and 2) decoded to a `Table` of notes (title "notes").

Columns: start_s, end_s, duration_s (seconds), pitch (MIDI note number), note (name, middle C = C4), velocity,
channel (1-16), track (0-based). Times come from the delta ticks through the tempo map (Set Tempo meta events of
every track, applied globally as format 1 requires; 120 BPM until the first one) or from the SMPTE division. A note
on with velocity 0 is a note off; running status is honoured; overlapping notes of the same pitch and channel end
first-in first-out; notes never ended stop at their track's end (counted in the notes). Track names, text, lyrics,
markers and cue points are the artifact's own words. Controllers, program changes, pitch bend and system-exclusive
data are counted, not interpreted.

Bounds: events counted against `max_elements`, tracks against `max_members`; delta and length numbers at most 4
bytes; nothing is read past a track chunk's end.
"""
from __future__ import annotations

import numpy as np

from .. import repr_b2 as R
from ..safe_b2 import Deadline, Reader, count, members, screen_words
from ..types import Column, Decoded, Malformed, Table
from . import Adapter

NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
WORD_META = {0x01: "text", 0x02: "copyright", 0x03: "track name", 0x04: "instrument", 0x05: "lyric", 0x06: "marker",
             0x07: "cue point"}
DATA_BYTES = {0x80: 2, 0x90: 2, 0xA0: 2, 0xB0: 2, 0xC0: 1, 0xD0: 1, 0xE0: 2}


def note_name(pitch: int) -> str:
    return f"{NAMES[pitch % 12]}{pitch // 12 - 1}"


def _vlq(r: Reader) -> int:
    value = 0
    for _ in range(4):
        b = r.u8()
        value = (value << 7) | (b & 0x7F)
        if not b & 0x80:
            return value
    raise Malformed("a MIDI variable-length number is longer than 4 bytes")


def _track_events(body: bytes, limits, deadline, counter):
    """(tick, kind, data) events of one track; kind 'on', 'off', 'tempo', 'word', 'other'."""
    r = Reader(body, ">", 0, "a MIDI track")
    tick, status, out = 0, None, []
    while r.remaining:
        tick += _vlq(r)
        counter[0] += 1
        if counter[0] > limits.max_elements:
            count(counter[0], limits, "MIDI events")
        if counter[0] % 65536 == 0:
            deadline.check("reading MIDI events")
        b = r.u8()
        if b == 0xFF:
            kind, length = r.u8(), _vlq(r)
            payload = r.take(length)
            if kind == 0x2F:
                out.append((tick, "end", None))
                return out
            if kind == 0x51 and length == 3:
                out.append((tick, "tempo", int.from_bytes(payload, "big")))
            elif kind in WORD_META:
                out.append((tick, "word", f"{WORD_META[kind]}: {payload.decode('latin-1').strip()}"))
            else:
                out.append((tick, "other", None))
            continue
        if b in (0xF0, 0xF7):
            r.skip(_vlq(r))
            out.append((tick, "other", None))
            status = None
            continue
        if b & 0x80:
            if b >= 0xF0:
                raise Malformed("a MIDI track holds a system message that a file may not contain")
            status = b
            first = r.u8()
        else:
            if status is None:
                raise Malformed("a MIDI data byte comes with no running status")
            first = b
        hi, ch = status & 0xF0, status & 0x0F
        second = r.u8() if DATA_BYTES[hi] == 2 else None
        if hi == 0x90 and second:
            out.append((tick, "on", (ch, first, second)))
        elif hi == 0x80 or (hi == 0x90 and second == 0):
            out.append((tick, "off", (ch, first, 0)))
        else:
            out.append((tick, "other", None))
    out.append((tick, "end", None))
    return out


def decode_midi_bytes(data: bytes, limits) -> tuple[Table, list[str], list[str]]:
    deadline = Deadline(limits)
    r = Reader(data, ">", 0, "the MIDI file")
    if r.take(4) != b"MThd":
        raise Malformed("the MIDI header chunk is missing")
    hlen = r.u32()
    if hlen < 6:
        raise Malformed("the MIDI header chunk is too short")
    fmt, ntracks, division = r.unpack("HHH")
    r.skip(hlen - 6)
    members(ntracks, limits, "MIDI tracks")
    if fmt > 2:
        raise Malformed(f"MIDI file format {fmt} does not exist")
    notes_out: list[str] = []
    tracks, counter = [], [0]
    while r.remaining >= 8 and len(tracks) < ntracks:
        cid, size = r.take(4), r.u32()
        body = r.take(min(size, r.remaining))
        if cid != b"MTrk":
            continue
        tracks.append(_track_events(body, limits, deadline, counter))
    if len(tracks) < ntracks:
        notes_out.append(f"the header declares {ntracks} tracks; {len(tracks)} were found")
    if division & 0x8000:
        fps = -((division >> 8) - 256)
        tpf = division & 0xFF
        if not fps or not tpf:
            raise Malformed("the MIDI SMPTE division is invalid")
        seconds_per_tick = 1.0 / ((29.97 if fps == 29 else fps) * tpf)

        def to_s(t):
            return t * seconds_per_tick
    else:
        if division == 0:
            raise Malformed("the MIDI division is zero")
        tempos = sorted({(t, us) for tr in tracks for t, k, us in tr if k == "tempo"})
        points = [(0, 0.0, 500_000)]
        for t, us in tempos:
            t0, s0, us0 = points[-1]
            points.append((t, s0 + (t - t0) * us0 / 1e6 / division, us))
        ticks_at = np.array([p[0] for p in points])

        def to_s(t):
            i = int(np.searchsorted(ticks_at, t, side="right")) - 1
            t0, s0, us0 = points[i]
            return s0 + (t - t0) * us0 / 1e6 / division
        if fmt == 2:
            notes_out.append("format 2 (independent sequences): the tempo map is applied to all tracks alike")
    rows, words, open_at_end, other = [], [], 0, 0
    for ti, events in enumerate(tracks):
        pending: dict[tuple[int, int], list[tuple[int, int]]] = {}
        end_tick = events[-1][0] if events else 0
        for tick, kind, val in events:
            if kind == "on":
                ch, pitch, vel = val
                pending.setdefault((ch, pitch), []).append((tick, vel))
            elif kind == "off":
                ch, pitch, _ = val
                started = pending.get((ch, pitch))
                if started:
                    t0, vel = started.pop(0)
                    rows.append((to_s(t0), to_s(tick), pitch, vel, ch + 1, ti))
            elif kind == "word":
                words.append(val)
            elif kind == "other":
                other += 1
        for (ch, pitch), starts in pending.items():
            for t0, vel in starts:
                rows.append((to_s(t0), to_s(end_tick), pitch, vel, ch + 1, ti))
                open_at_end += 1
    rows.sort(key=lambda x: (x[0], x[5], x[4], x[2]))
    if open_at_end:
        notes_out.append(f"{open_at_end} note(s) never ended and stop at their track's end")
    if other:
        notes_out.append(f"{other} controller, program, pitch-bend, system-exclusive or other meta event(s) "
                         "counted, not interpreted")
    start = np.array([x[0] for x in rows], np.float64)
    end = np.array([x[1] for x in rows], np.float64)
    pitch = np.array([x[2] for x in rows], np.int64)
    cols = (Column("start_s", "number", "s", start, 0), Column("end_s", "number", "s", end, 0),
            Column("duration_s", "number", "s", end - start, 0), Column("pitch", "integer", None, pitch, 0),
            Column("note", "text", None, tuple(note_name(int(p)) for p in pitch), 0),
            Column("velocity", "integer", None, np.array([x[3] for x in rows], np.int64), 0),
            Column("channel", "integer", None, np.array([x[4] for x in rows], np.int64), 0),
            Column("track", "integer", None, np.array([x[5] for x in rows], np.int64), 0))
    notes_out.append(f"MIDI format {fmt}, {len(tracks)} track(s), " +
                     ("SMPTE time" if division & 0x8000 else f"{division} ticks per quarter note"))
    return Table(cols, len(rows), "notes"), words, notes_out


class MIDIAdapter(Adapter):
    name = "b2-midi"
    formats = ("midi",)
    families = ("audio",)

    def decode(self, data, detected, limits):
        table, words, notes = decode_midi_bytes(data, limits)
        text = "\n".join(w for w in words if w)
        notes = notes + list(screen_words(text))
        rep = R.Table(table.columns, table.n_rows, table.title, words=text)
        return Decoded(detected, rep, tuple(notes), False)


ADAPTERS = (MIDIAdapter(),)

__all__ = ["ADAPTERS", "MIDIAdapter", "decode_midi_bytes", "note_name"]
