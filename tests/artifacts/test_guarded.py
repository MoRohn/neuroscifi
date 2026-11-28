"""The guarded child (fix lane F1, ruling A.1): every decode of bytes the owner hands over first runs in a child process
under RLIMIT_AS / RLIMIT_CPU and a wall-clock kill; only a file the child read within bounds is decoded here.

The hostile inputs are the reviewers' kind (a decode whose working set the adapter's arithmetic does not see); the
limits are made small through the parent's own functions (`address_space`, `wall_seconds`) so each case is cheap and
cannot depend on how much memory this host has."""
import struct
import sys
import zlib

import pytest

from neuroscifi.artifacts import adapters, guarded, inspect_cli
from neuroscifi.artifacts import store as A
from neuroscifi.artifacts.types import Limits, Refused
from neuroscifi.instance import store

T0 = 1_800_000_000.0
CSV = b"x,y\n1,2\n3,4\n5,6\n"


def png(width, height):
    """An all-zero 8-bit RGB PNG (hand-assembled): small on disk, width*height*3 bytes of pixels when decoded."""
    def chunk(kind, body):
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))
    raw = (b"\x00" + b"\x00" * (width * 3)) * height
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


@pytest.fixture
def individual(tmp_path, guard_on):
    db = store.create(tmp_path / "individual.sqlite", T0)
    try:
        yield db
    finally:
        db.close()


def test_an_honest_file_is_read_in_the_child_then_here(guard_on):
    verdict = guarded.check(CSV, name="t.csv")
    assert verdict["status"] == "decoded" and verdict["kind"] == "table"
    if sys.platform.startswith("linux"):
        assert verdict["memory_limit"] and verdict["cpu_limit"]
    here = adapters.decode(CSV, name="t.csv")
    there = guarded.decode(CSV, name="t.csv")
    assert there.representation.describe() == here.representation.describe()


def test_the_limits_follow_the_stated_factors():
    lim = Limits()
    assert guarded.address_space(lim, 64 << 20) == (256 + 128 + 512) << 20
    assert guarded.wall_seconds(lim) == lim.max_seconds + 5


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="RLIMIT_AS is enforced on Linux")
def test_over_memory_is_refused_and_nothing_is_decoded_here(monkeypatch, guard_on):
    data = png(4000, 3000)                       # 36 MB of pixels: within max_decoded_bytes, over a 160 MiB space
    monkeypatch.setattr(guarded, "address_space", lambda limits, size: 160 << 20)
    called = []
    monkeypatch.setattr(adapters, "decode", lambda *a, **k: called.append(1))
    with pytest.raises(Refused) as caught:
        guarded.decode(data, name="big.png")
    assert caught.value.rule == "resource_limit" and not called
    assert "needed more memory or time than is allowed here: 160 MiB / 25 s" in str(caught.value)


def test_over_time_is_refused(monkeypatch, guard_on):
    monkeypatch.setattr(guarded, "wall_seconds", lambda limits: 0.01)
    with pytest.raises(Refused) as caught:
        guarded.decode(CSV, name="t.csv")
    assert caught.value.rule == "resource_limit" and "stopped after the time allowed" in str(caught.value)


def test_a_child_that_ends_without_an_answer_is_refused(monkeypatch, guard_on):
    monkeypatch.setattr(guarded.sys, "executable", "/bin/false")
    with pytest.raises(Refused) as caught:
        guarded.decode(CSV, name="t.csv")
    assert caught.value.rule == "resource_limit" and "without an answer" in str(caught.value)


def test_no_child_at_all_is_named(monkeypatch, guard_on):
    monkeypatch.setattr(guarded.sys, "executable", "/nonexistent/python")
    with pytest.raises(Refused) as caught:
        guarded.decode(CSV, name="t.csv")
    assert caught.value.rule == "guard_unavailable"


def test_the_decoders_own_deadline_is_not_spent_twice(monkeypatch, guard_on):
    calls = []
    real = adapters.decode
    monkeypatch.setattr(adapters, "decode", lambda *a, **k: calls.append(1) or real(*a, **k))
    with pytest.raises(Exception) as caught:
        guarded.decode(CSV, name="t.csv", limits=Limits(max_seconds=0.0))
    assert getattr(caught.value, "reason", None) == "timeout" and not calls


def test_it_works_with_no_home(monkeypatch, guard_on):
    monkeypatch.delenv("HOME", raising=False)
    assert guarded.check(CSV, name="t.csv")["status"] == "decoded"


def test_the_store_keeps_a_refused_file_unread(individual, monkeypatch):
    monkeypatch.setattr(guarded, "wall_seconds", lambda limits: 0.01)
    record = A.add(individual, CSV, name="t.csv")
    error = record.index["error"]
    assert error["rule"] == "resource_limit" and "representation" not in record.index
    assert A.read_bytes(individual, record.id) == CSV


def test_inspect_says_it_plainly(monkeypatch, guard_on):
    monkeypatch.setattr(guarded, "wall_seconds", lambda limits: 0.01)
    with pytest.raises(Refused) as caught:
        inspect_cli.report(CSV, "t.csv")
    assert "Refused" not in str(caught.value) and "MiB" in str(caught.value)


def test_the_parent_decodes_only_after_the_child(monkeypatch, guard_on):
    order = []
    real_check, real_decode = guarded.check, adapters.decode
    monkeypatch.setattr(guarded, "check", lambda *a, **k: order.append("child") or real_check(*a, **k))
    monkeypatch.setattr(adapters, "decode", lambda *a, **k: order.append("here") or real_decode(*a, **k))
    guarded.decode(CSV, name="t.csv")
    assert order == ["child", "here"]


def test_the_same_file_twice_is_the_same_artifact(tmp_path):
    """Code review L13 (fix lane F1, ruling F): same bytes and name -> the held artifact; another name -> a second
    artifact sharing the bytes."""
    db = store.create(tmp_path / "i.sqlite", T0)
    try:
        first = A.add(db, CSV, name="t.csv", now=T0)
        again = A.add(db, CSV, name="t.csv", now=T0 + 1)
        other = A.add(db, CSV, name="copy.csv", now=T0 + 2)
        assert again.id == first.id and other.id != first.id
        assert db.execute("SELECT count(*) FROM artifacts").fetchone()[0] == 2
    finally:
        db.close()


def test_a_sidecar_swapped_while_it_is_opened_is_refused(tmp_path, monkeypatch):
    """Security review N3: the sidecar was opened without following a link, then reopened by path. Its mode is now
    set on that descriptor, and the file sqlite opens must be the same file."""
    import os
    import sqlite3
    db = store.create(tmp_path / "i.sqlite", T0)
    real = sqlite3.connect

    def swap(path, *a, **k):
        if str(path).endswith(".artifacts.sqlite"):
            os.replace(tmp_path / "planted", path)
        return real(path, *a, **k)
    (tmp_path / "planted").write_bytes(b"")
    monkeypatch.setattr(A.sqlite3, "connect", swap)
    try:
        with pytest.raises(A.StoreRefused) as caught:
            A.add(db, CSV, name="t.csv", now=T0)
        assert caught.value.rule == "sidecar"
    finally:
        db.close()
