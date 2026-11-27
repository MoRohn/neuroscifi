"""Fix lane F3, item 5: the bounds fix lane F1 left (rulings A and F)."""
from __future__ import annotations

import tracemalloc

from neuroscifi.artifacts import adapters
from neuroscifi.artifacts import types as T


def _csv(rows: int) -> bytes:
    lines = ["id,day,value,label"]
    lines += [f"{i},2026-{1 + i % 12:02d}-{1 + i % 28:02d},{(i * 7919) % 1000 / 7:.3f},{'nsew'[i % 4]}"
              for i in range(rows)]
    return ("\n".join(lines) + "\n").encode()


def test_a_large_honest_csv_is_read_within_the_decoded_bytes_limit():
    data = _csv(200_000)                                   # about 6 MiB
    budget = 16 << 20
    limits = T.Limits(max_decoded_bytes=budget)
    tracemalloc.start()
    try:
        decoded = adapters.decode(data, name="big.csv", limits=limits)
        _now, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert decoded.partial and 0 < decoded.representation.n_rows < 200_000
    assert any("stopped after" in n for n in decoded.notes)
    # before: an io.StringIO copy of the text (four bytes a character) came on top of the rows: about 2x the limit
    assert peak <= budget + len(data), (peak >> 20, budget >> 20)


def test_a_csv_whose_text_alone_is_over_the_limit_is_refused_before_it_is_split():
    data = _csv(200_000)
    try:
        adapters.decode(data, name="big.csv", limits=T.Limits(max_decoded_bytes=len(data) // 2))
    except T.Refused as error:
        assert error.rule == "max_decoded_bytes"
    else:
        raise AssertionError("a text over the limit was split into rows")
