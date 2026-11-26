"""NumPy .npy/.npz (lane B2): header validated before numpy touches the data; tables recovered; object arrays,
size lies and zip bombs refused."""
import io
import struct
import zipfile
import zlib

import numpy as np
import pytest

from neuroscifi.artifacts import adapters
from neuroscifi.artifacts import types as T
from neuroscifi.artifacts.adapters import _b2

_b2.register_all()


def _npy(header: str, payload: bytes, version=1) -> bytes:
    h = header.encode("latin-1")
    pad = 64 - (10 + len(h) + 1) % 64
    h = h + b" " * pad + b"\n"
    if version == 1:
        return b"\x93NUMPY\x01\x00" + struct.pack("<H", len(h)) + h + payload
    return b"\x93NUMPY\x02\x00" + struct.pack("<I", len(h)) + h + payload


def _saved(arr) -> bytes:
    buf = io.BytesIO()
    np.save(buf, arr, allow_pickle=False)
    return buf.getvalue()


def test_hand_written_1d_float_and_2d_fortran_int():
    data = _npy("{'descr': '<f8', 'fortran_order': False, 'shape': (3,), }", struct.pack("<3d", 1.5, float("nan"), -2))
    out = adapters.decode(data)
    assert out.detected.format == "npy"
    col = out.representation.columns[0]
    assert col.kind == "number" and col.n_missing == 1 and col.values[0] == 1.5 and col.values[2] == -2
    # 2 x 3 int32 in Fortran order: memory holds columns one after another
    data = _npy("{'descr': '<i4', 'fortran_order': True, 'shape': (2, 3), }", struct.pack("<6i", 1, 4, 2, 5, 3, 6))
    t = adapters.decode(data).representation
    assert [c.name for c in t.columns] == ["c0", "c1", "c2"] and t.n_rows == 2
    assert [c.values.tolist() for c in t.columns] == [[1, 4], [2, 5], [3, 6]]


def test_numpy_written_kinds_round_trip():
    for arr, kind in [(np.array([True, False]), "boolean"), (np.array([1, 2], np.uint8), "integer"),
                      (np.array(["ab", "c"]), "text"),
                      (np.array(["2020-01-01T00:00:01", "NaT"], dtype="datetime64[s]"), "time")]:
        col = adapters.decode(_saved(arr)).representation.columns[0]
        assert col.kind == kind
    col = adapters.decode(_saved(np.array(["2020-01-01T00:00:01"], dtype="datetime64[ms]"))).representation.columns[0]
    assert col.values[0] == 1577836801.0


def test_3d_and_complex_are_described_only():
    out = adapters.decode(_saved(np.arange(24, dtype=np.float32).reshape(2, 3, 4)))
    rep = out.representation
    assert isinstance(rep, T.Opaque) and "shape: (2, 3, 4)" in rep.findings and "max: 23" in rep.findings
    assert isinstance(adapters.decode(_saved(np.array([1 + 2j]))).representation, T.Opaque)


def test_object_arrays_structured_and_hostile_headers():
    with pytest.raises(T.Refused) as err:
        adapters.decode(_npy("{'descr': '|O', 'fortran_order': False, 'shape': (1,), }", b"\x80\x04N."))
    assert err.value.rule == "pickle"
    with pytest.raises(T.Unsupported):
        adapters.decode(_saved(np.zeros(2, dtype=[("a", "<i4"), ("b", "<f8")])))
    with pytest.raises(T.Refused) as err:       # 32 TiB declared: refused before allocation
        adapters.decode(_npy("{'descr': '<f8', 'fortran_order': False, 'shape': (65536, 65536, 1024), }", b""))
    assert err.value.rule == "max_decoded_bytes"
    with pytest.raises(T.Malformed):            # fewer bytes than the header needs
        adapters.decode(_npy("{'descr': '<f8', 'fortran_order': False, 'shape': (10,), }", b"\x00" * 16))
    with pytest.raises(T.Malformed):            # code instead of a dict literal: never evaluated
        adapters.decode(_npy("__import__('os').system('true')", b""))


def _npz(members, compress=zipfile.ZIP_STORED):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compress) as z:
        for name, data in members:
            z.writestr(name, data)
    return buf.getvalue()


def test_npz_bundle_of_arrays():
    data = _npz([("x.npy", _saved(np.array([1.0, 2.0]))), ("y.npy", _saved(np.eye(2)))], zipfile.ZIP_DEFLATED)
    out = adapters.decode(data, name="a.npz")
    assert out.detected.format == "npz"
    b = out.representation
    assert isinstance(b, T.Bundle) and [m.path for m in b.members] == ["x.npy", "y.npy"]
    assert b.members[1].inner.columns[1].values.tolist() == [0.0, 1.0]


def test_npz_bomb_and_size_lies():
    bomb = _npz([("z.npy", _saved(np.zeros(5_000_000)))], zipfile.ZIP_DEFLATED)
    with pytest.raises(T.Refused) as err:
        adapters.decode(bomb)
    assert err.value.rule == "max_ratio"
    data = bytearray(_npz([("x.npy", _saved(np.array([1.0, 2.0])))]))
    at = data.find(b"PK\x01\x02")
    struct.pack_into("<I", data, at + 24, 10)                      # central directory: uncompressed size lies
    with pytest.raises(T.Malformed):
        adapters.decode(bytes(data))
    data = bytearray(_npz([("x.npy", _saved(np.array([1.0, 2.0])))]))
    struct.pack_into("<I", data, data.find(b"PK\x01\x02") + 16, zlib.crc32(b"no") & 0xFFFFFFFF)
    with pytest.raises(T.Malformed):
        adapters.decode(bytes(data))
