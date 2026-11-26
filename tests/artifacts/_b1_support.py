"""Shared test support for lane B1's adapters: a fresh registry holding only B1's adapters, with B1's detection
refinement in front of K's `detect` (until K's full table is merged), and small byte builders."""
import io
import struct
import zlib

import pytest

from neuroscifi.artifacts import adapters, detect as detect_mod, detect_b1
from neuroscifi.artifacts.adapters import _b1


@pytest.fixture
def b1reg(monkeypatch):
    monkeypatch.setattr(adapters, "_ADAPTERS", [])
    monkeypatch.setattr(adapters, "_GAP_HOOKS", [])
    monkeypatch.setattr(adapters, "_DISCOVERED", [])
    monkeypatch.setattr(detect_mod, "detect", detect_b1.detect)
    _b1.register_all()
    return adapters


def make_zip(members, *, method=8, flags=0, extra_central=None, symlinks=()):
    """A ZIP assembled by hand: members is a list of (name, bytes). Deflate via zlib (raw), CRC by zlib.crc32."""
    out, central = io.BytesIO(), []
    for name, data in members:
        raw_name = name.encode("utf-8")
        if method == 8:
            co = zlib.compressobj(9, zlib.DEFLATED, -15)
            comp = co.compress(data) + co.flush()
        else:
            comp = data
        crc = zlib.crc32(data)
        offset = out.tell()
        out.write(struct.pack("<4s5H3L2H", b"PK\x03\x04", 20, flags | 0x800, method, 0, 0, crc, len(comp), len(data),
                              len(raw_name), 0))
        out.write(raw_name)
        out.write(comp)
        mode = (0o120777 if name in symlinks else 0o100644) << 16
        central.append(struct.pack("<4s6H3L5H2L", b"PK\x01\x02", (3 << 8) | 20, 20, flags | 0x800, method, 0, 0, crc,
                                   len(comp), len(data), len(raw_name), 0, 0, 0, 0, mode, offset) + raw_name)
    cd_offset = out.tell()
    for c in central:
        out.write(c)
    cd_size = out.tell() - cd_offset
    n = len(central) if extra_central is None else extra_central
    out.write(struct.pack("<4s4H2LH", b"PK\x05\x06", 0, 0, n, n, cd_size, cd_offset, 0))
    return out.getvalue()
