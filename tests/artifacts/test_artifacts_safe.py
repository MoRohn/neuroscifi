"""artifacts/safe.py: bounded reading, decompression bombs, hostile ZIPs, refused XML, cell parsing."""
import bz2
import gzip
import io
import lzma
import os
import struct
import zipfile
import zlib

import numpy as np
import pytest
import zstandard

from neuroscifi.artifacts import safe
from neuroscifi.artifacts.types import ArtifactError, Limits, Malformed, Refused, Unsupported

L = Limits()


# --- files -----------------------------------------------------------------------------------------------------------

def test_read_file_bounds_and_refusals(tmp_path):
    f = tmp_path / "a.bin"
    f.write_bytes(b"x" * 100)
    assert safe.read_file(f, 100) == b"x" * 100
    with pytest.raises(Refused) as e:
        safe.read_file(f, 99)
    assert e.value.reason == "too_large"
    link = tmp_path / "link"
    link.symlink_to(f)
    with pytest.raises(Refused) as e:
        safe.read_file(link, 1000)
    assert e.value.rule == "symlink"
    with pytest.raises(Refused) as e:
        safe.read_file(tmp_path, 1000)
    assert e.value.rule == "not_regular"
    fifo = tmp_path / "fifo"
    os.mkfifo(fifo)
    with pytest.raises(Refused):
        safe.read_file(fifo, 1000)   # must not block
    with pytest.raises(Refused) as e:
        safe.read_file(tmp_path / "missing", 1000)
    assert e.value.rule == "not_found"


def test_deadline_and_budget():
    ticks = iter([0.0, 0.5, 2.0])
    deadline = safe.Deadline(1.0, clock=lambda: next(ticks))
    deadline.check()
    with pytest.raises(ArtifactError) as e:
        deadline.check()
    assert e.value.reason == "timeout"
    budget = safe.Budget(10, "max_elements")
    budget.take(10)
    with pytest.raises(Refused):
        budget.take(1)


# --- streams ---------------------------------------------------------------------------------------------------------

PAYLOAD = bytes(range(256)) * 64


@pytest.mark.parametrize("codec,pack", [("gzip", lambda b: gzip.compress(b, mtime=0)), ("zlib", zlib.compress),
                                        ("bzip2", bz2.compress), ("xz", lzma.compress),
                                        ("zstd", lambda b: zstandard.ZstdCompressor().compress(b))])
def test_round_trip(codec, pack):
    out, notes = safe.decompress(pack(PAYLOAD), codec, L)
    assert out == PAYLOAD


@pytest.mark.parametrize("codec,pack", [("gzip", lambda b: gzip.compress(b, mtime=0)), ("bzip2", bz2.compress),
                                        ("xz", lzma.compress),
                                        ("zstd", lambda b: zstandard.ZstdCompressor().compress(b))])
def test_bombs_are_refused_before_they_are_held(codec, pack):
    bomb = pack(b"\x00" * (20 << 20))
    assert len(bomb) * L.max_ratio < (20 << 20)   # really a bomb by the ratio rule
    with pytest.raises(Refused) as e:
        safe.decompress(bomb, codec, L)
    assert e.value.rule == "max_ratio" and e.value.reason == "too_large"
    small_total = Limits(max_decoded_bytes=1000)
    with pytest.raises(Refused) as e:
        safe.decompress(pack(PAYLOAD), codec, small_total)
    assert e.value.rule == "max_decoded_bytes"


def test_ratio_floor_allows_small_repetitive_streams():
    out, _ = safe.decompress(gzip.compress(b"a" * 500_000), "gzip", L)
    assert len(out) == 500_000


@pytest.mark.parametrize("codec,pack", [("gzip", gzip.compress), ("bzip2", bz2.compress), ("xz", lzma.compress),
                                        ("zstd", lambda b: zstandard.ZstdCompressor(write_checksum=True).compress(b))])
def test_truncated_and_corrupt_streams_are_malformed(codec, pack):
    good = pack(PAYLOAD)
    with pytest.raises(Malformed):
        safe.decompress(good[: len(good) // 2], codec, L)
    corrupt = bytearray(good)
    corrupt[len(corrupt) // 2] ^= 0xFF
    corrupt[len(corrupt) // 2 + 1] ^= 0xFF
    with pytest.raises(Malformed):   # every one of these formats carries a checksum
        safe.decompress(bytes(corrupt), codec, L)


def test_concatenated_members_are_bounded():
    data = gzip.compress(b"a") * 5
    out, notes = safe.decompress(data, "gzip", L)
    assert out == b"aaaaa" and any("5 concatenated" in n for n in notes)
    with pytest.raises(Refused):
        safe.decompress(data, "gzip", L, max_members=4)


def test_trailing_garbage_is_noted_not_decoded():
    out, notes = safe.decompress(gzip.compress(b"abc") + b"JUNK", "gzip", L)
    assert out == b"abc" and any("not decoded" in n for n in notes)


# --- zip -------------------------------------------------------------------------------------------------------------

def _zip(members, method=zipfile.ZIP_DEFLATED):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", method) as z:
        for name, data in members:
            z.writestr(name, data)
    return buf.getvalue()


def test_zip_reads_members_and_never_uses_names_as_paths(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    data = _zip([("../../evil.txt", b"x"), ("/abs/path", b"y"), ("ok.txt", b"hello")])
    z = safe.SafeZip(data, L)
    assert z.names() == ["../../evil.txt", "/abs/path", "ok.txt"]
    assert z.read("ok.txt") == b"hello" and z.read("../../evil.txt") == b"x"
    assert list(tmp_path.iterdir()) == [] and not os.path.exists("/abs/path")


def test_zip_member_bomb_and_total_budget():
    bomb = _zip([("zeros", b"\x00" * (30 << 20))])
    with pytest.raises(Refused):
        safe.SafeZip(bomb, L).read("zeros")
    many = _zip([(f"m{i}", PAYLOAD) for i in range(10)])
    z = safe.SafeZip(many, Limits(max_decoded_bytes=len(PAYLOAD) * 3 + 10))
    for i in range(3):
        z.read(f"m{i}")
    with pytest.raises(Refused):
        z.read("m3")


def test_zip_lying_sizes_and_crc():
    data = bytearray(_zip([("a.txt", b"hello world")], method=zipfile.ZIP_STORED))
    # corrupt one stored byte: the CRC check must catch it
    at = data.find(b"hello world")
    data[at] = ord("j")
    with pytest.raises(Malformed):
        safe.SafeZip(bytes(data), L).read("a.txt")


def test_zip_member_count_is_checked_before_the_directory_is_built():
    data = _zip([(f"f{i}", b"") for i in range(20)])
    with pytest.raises(Refused) as e:
        safe.SafeZip(data, Limits(max_members=10))
    assert e.value.rule == "max_members"
    eocd = data.rfind(b"PK\x05\x06")
    forged = data[:eocd + 10] + struct.pack("<H", 60000) + data[eocd + 12:]
    assert safe.zip_declared_entries(forged) == 60000
    with pytest.raises(Refused):
        safe.SafeZip(forged, L)


def test_zip_encrypted_and_unknown_methods_are_unsupported():
    data = bytearray(_zip([("a", b"x")], method=zipfile.ZIP_STORED))
    for sig in (b"PK\x03\x04", b"PK\x01\x02"):
        at = data.find(sig)
        flag_at = at + (6 if sig == b"PK\x03\x04" else 8)
        data[flag_at] |= 1
    with pytest.raises(Unsupported):
        safe.SafeZip(bytes(data), L).read("a")


def test_not_a_zip_is_malformed():
    with pytest.raises(Malformed):
        safe.SafeZip(b"PK\x03\x04 nothing else", L)


# --- xml -------------------------------------------------------------------------------------------------------------

LAUGHS = b"""<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol"><!ENTITY lol2 "&lol;&lol;&lol;&lol;">]>
<lolz>&lol2;</lolz>"""
XXE = b"""<?xml version="1.0"?><!DOCTYPE r [<!ENTITY x SYSTEM "file:///etc/passwd">]><r>&x;</r>"""


def test_xml_parses_with_namespaces():
    root = safe.parse_xml(b'<a xmlns="urn:x"><b k="v">t</b></a>', L)
    assert root.tag == "{urn:x}a" and root[0].attrib == {"k": "v"} and root[0].text == "t"


@pytest.mark.parametrize("doc", [LAUGHS, XXE, b'<!DOCTYPE html><html/>'])
def test_xml_declarations_are_refused(doc):
    with pytest.raises(Refused) as e:
        safe.parse_xml(doc, L)
    assert e.value.rule == "xml_dtd"


def test_xml_bounds():
    deep = b"<a>" * 300 + b"</a>" * 300
    with pytest.raises(Refused) as e:
        safe.parse_xml(deep, L)
    assert e.value.rule == "xml_depth"
    wide = b"<r>" + b"<i/>" * 100 + b"</r>"
    with pytest.raises(Refused) as e:
        safe.parse_xml(wide, Limits(max_elements=50))
    assert e.value.rule == "max_elements"
    with pytest.raises(Malformed):
        safe.parse_xml(b"<a><b></a>", L)
    with pytest.raises(Malformed):
        safe.parse_xml(b"&undefined;", L)
    with pytest.raises(ArtifactError):
        safe.parse_xml(b"<a>&undefined;</a>", L)


# --- cells -----------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("text,value", [("3", 3.0), ("-2.5", -2.5), (" 1e3 ", 1000.0), (".5", 0.5), ("1,234", 1234.0),
                                        ("1,234,567.25", 1234567.25), ("1_000", 1000.0), ("12 345", 12345.0),
                                        ("1,23", None), ("12,3456", None), ("nan", None), ("inf", None),
                                        ("1e999", None), ("3 apples", None), ("", None), ("0x10", None)])
def test_parse_number(text, value):
    assert safe.parse_number(text) == value


@pytest.mark.parametrize("text,value", [("2024-01-01", 1704067200.0), ("2024-01-01T00:00:00Z", 1704067200.0),
                                        ("2024-01-01T02:00:00+02:00", 1704067200.0),
                                        ("2024-01-01 00:00:01.5", 1704067201.5), ("2024-02-30", None),
                                        ("01/02/2024", None), ("yesterday", None)])
def test_parse_time(text, value):
    assert safe.parse_time(text) == value


def test_column_kinds_are_narrowest_without_guessing():
    assert safe.column_from_values("n", [1, 2, 3]).kind == "integer"
    c = safe.column_from_values("n", [1, None, 3])
    assert c.kind == "integer" and c.n_missing == 1 and np.isnan(c.values[1])
    assert safe.column_from_values("x", ["1.5", "2", "NA"]).kind == "number"
    assert safe.column_from_values("b", [True, False, None]).kind == "boolean"
    assert safe.column_from_values("b", ["yes", "no"]).kind == "boolean"
    t = safe.column_from_values("t", ["2024-01-01", "2024-01-02"])
    assert t.kind == "time" and t.values[1] - t.values[0] == 86400
    mixed = safe.column_from_values("m", ["1", "2", "three"])
    assert mixed.kind == "text" and mixed.values == ("1", "2", "three")
    assert safe.column_from_values("e", []).kind == "text"
