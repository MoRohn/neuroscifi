"""detect.detect against hand-assembled bytes of known format (tests/artifacts/detect_corpus.py): every table
entry, conflicting hints, truncated prefixes, random and adversarial bytes, and time bounds."""
import os
import sys
import time

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(__file__))
from detect_corpus import conflicts, corpus  # noqa: E402

from neuroscifi.artifacts.detect import _REFINES, FORMATS, HEAD_MAX, detect  # noqa: E402
from neuroscifi.artifacts.types import FAMILIES  # noqa: E402

# A truncated prefix may legitimately be called by a more general name: its container, or the format it extends.
GENERAL = {"docx": {"zip"}, "xlsx": {"zip"}, "pptx": {"zip"}, "odt": {"zip"}, "ods": {"zip"}, "odp": {"zip"},
           "epub": {"zip"}, "jar": {"zip"}, "apk": {"zip"}, "npz": {"zip"}, "doc": {"ole"}, "xls": {"ole"},
           "ppt": {"ole"}, "ipynb": {"json"}, "geojson": {"json"}, "ndjson": {"json"}, "atom": {"xml"},
           "rss": {"xml"}, "kml": {"xml"}, "gpx": {"xml"}, "plist": {"xml"}, "svg": {"xml"},
           "pyproject": {"toml"}, "package-json": {"json"}}


def _run(data, kw):
    kw = dict(kw)
    kw.setdefault("size", len(data))
    return detect(data, **kw)


@pytest.mark.parametrize("label,data,kw,fmt,basis", corpus(), ids=[c[0] for c in corpus()])
def test_every_table_entry_is_identified(label, data, kw, fmt, basis):
    d = _run(data, kw)
    assert (d.format, d.basis) == (fmt, basis), d.reasons
    assert d.family in FAMILIES and (d.family, d.media_type) == FORMATS[fmt]
    assert d.reasons and all(isinstance(r, str) and r for r in d.reasons)


@pytest.mark.parametrize("label,data,kw,fmt,basis", conflicts(), ids=[c[0] for c in conflicts()])
def test_conflicts_and_adversarial_bytes(label, data, kw, fmt, basis):
    d = _run(data, kw)
    assert (d.format, d.basis) == (fmt, basis), d.reasons


def test_a_contradicting_hint_loses_and_says_so():
    png = next(c[1] for c in corpus() if c[0] == "png")
    d = detect(png, name="data.csv", declared="text/plain")
    assert d.format == "png"
    assert any("text/plain" in r and "signature wins" in r for r in d.reasons)
    assert any(".csv" in r and "signature wins" in r for r in d.reasons)


def test_a_consistent_hint_refines_but_is_marked_declared():
    zipped = next(c[1] for c in corpus() if c[0] == "zip")
    # only a prefix of a longer archive is seen: a member specific to docx may lie beyond it, so the name refines
    d = detect(zipped, name="report.docx", size=len(zipped) + (1 << 20))
    assert (d.format, d.basis, d.container) == ("docx", "declared", "zip")
    assert any("not confirmed" in r for r in d.reasons)
    # the whole archive is seen and nothing in it is specific to docx: the name does not refine it (lane FX)
    d = detect(zipped, name="report.docx")
    assert (d.format, d.basis) == ("zip", "signature")
    assert any("nothing in it is specific to docx" in r for r in d.reasons)


def test_hint_only_when_nothing_is_recognised():
    d = detect(b"\x00\x01\x02\x03\xfe", name="model.pkl")
    assert (d.format, d.basis) == ("pickle", "declared")
    d = detect(b"\x00\x01\x02\x03\xfe")
    assert (d.format, d.basis, d.family) == ("unknown", "none", "unknown")


@pytest.mark.parametrize("n", [1, 4, 16])
def test_truncated_prefixes_never_raise_and_never_misname(n):
    for label, data, kw, fmt, _ in corpus():
        for size in (None, len(data)):
            d = detect(data[:n], name=kw.get("name"), declared=kw.get("declared"), size=size)
            # a name may refine a generic answer on a prefix of a longer file (lane FX: the full decode then
            # confirms or withdraws it; a ZIP named .docx is "zip" once its whole directory is seen)
            hinted = _REFINES.get(fmt, set()) if size is not None and n < len(data) else set()
            assert d.format in {fmt, "unknown", "text"} | GENERAL.get(fmt, set()) | hinted, (label, n, d)


def test_random_bytes_are_unknown():
    rng = np.random.default_rng(20261126)
    named = []
    for _ in range(3000):
        n = int(rng.integers(16, 4096))
        d = detect(rng.bytes(n), size=n)
        if d.format != "unknown":
            named.append(d.format)
    assert named == []


def test_garbage_inputs_never_raise():
    for value in (None, 5, "text", object(), bytearray(b"PK\x03\x04"), memoryview(b"\x89PNG")):
        assert detect(value).family in FAMILIES
    assert detect(b"x", size=-3).format == "text"
    assert detect(b"x", name=5, declared=object()).format == "text"


ADVERSARIAL = {
    "brackets": b"[" * HEAD_MAX, "braces": b"{" * HEAD_MAX, "lt": b"<" * HEAD_MAX,
    "xml-comment": b"<!--" + b"-" * HEAD_MAX, "doctype": b"<!DOCTYPE [" + b"a" * HEAD_MAX,
    "md-link": b"[a](" * (HEAD_MAX // 4), "bold": b"**a" * (HEAD_MAX // 3), "table-rule": (b"|---" * 100 + b"x\n") * 160,
    "nested-json": b'[{"a":' * 10000, "obj-lines": b"v 1 1 1\n" * 8000, "pk-headers": b"PK\x03\x04" * (HEAD_MAX // 4),
    "commas": (b"a," * 50 + b"\n") * 600, "ndjson": b"{}\n" * 20000, "pbm-comments": b"P1\n" + b"#\n" * 20000,
}


@pytest.mark.parametrize("name", sorted(ADVERSARIAL))
def test_adversarial_prefixes_are_fast(name):
    data = ADVERSARIAL[name]
    for size in (None, len(data), 2 * len(data)):
        started = time.perf_counter()
        detect(data, size=size)
        assert time.perf_counter() - started < 1.0
