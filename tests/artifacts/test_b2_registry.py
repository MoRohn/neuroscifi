"""Lane B2 as a whole: every format is detected from its bytes (hints lose to signatures), every adapter is in the
live catalogue, every sample decodes or answers HeaderOnly, header-only answers are counted as gaps, and random
corruption of every sample only ever ends in a named ArtifactError within the deadline."""
import random
import time
import zlib

import pytest

from b2_fixtures import b2_registered  # noqa: F401

from b2_corpus import samples
from neuroscifi.artifacts import adapters, detect
from neuroscifi.artifacts import types as T
from neuroscifi.artifacts.adapters import _b2
from neuroscifi.artifacts.types import HeaderOnly

SAMPLES = samples()
HEADER_ONLY = {"jpeg", "webp", "heic", "psd", "mp3", "flac", "opus", "aac", "m4a"}


def test_register_all_is_idempotent_and_catalogued():
    assert _b2.register_all() == []
    rows = {r["name"]: r for r in adapters.catalog()}
    for adapter in _b2.adapters():
        assert rows[adapter.name]["available"] is True and rows[adapter.name]["source"] == "builtin"
    covered = {f for a in _b2.adapters() for f in a.formats}
    assert {fmt for fmt, _ in SAMPLES.values()} <= covered


@pytest.mark.parametrize("name", sorted(SAMPLES))
def test_detected_from_the_bytes_alone(name):
    fmt, data = SAMPLES[name]
    det = detect.detect(data[:detect.HEAD_MAX], size=len(data))
    assert det.format == fmt, det
    assert det.basis in ("signature", "structure") and det.reasons


def test_a_contradicting_name_and_declared_type_lose():
    fmt, data = SAMPLES["gif"]
    det = detect.detect(data, name="photo.png", declared="image/png", size=len(data))
    assert det.format == "gif" and any("wins" in r for r in det.reasons)
    # bytes that show no STL structure are not an STL because of their name (lane FX, ADR-080)
    det = detect.detect(b"\x00\x01\x02\x03" * 20, name="model.stl", size=80)
    assert det.format == "unknown" and any("never identifies" in r for r in det.reasons)


@pytest.mark.parametrize("name", sorted(SAMPLES))
def test_every_sample_decodes_or_is_header_only(name):
    fmt, data = SAMPLES[name]
    if fmt in HEADER_ONLY:
        with pytest.raises(HeaderOnly) as err:
            adapters.decode(data)
        assert err.value.needs and err.value.facts
        return
    out = adapters.decode(data)
    rep = out.representation
    assert isinstance(rep, T.REPRESENTATIONS) and not isinstance(rep, T.Opaque) or fmt == "npy"
    d = rep.describe()
    assert isinstance(d, dict) and len(repr(d)) < 64 * 1024
    assert isinstance(_b2.content_shape(rep), T.ContentShape)


def test_header_only_is_counted_as_an_unsupported_gap():
    seen = []
    remove = adapters.on_gap(lambda det, outcome: seen.append((det.format, outcome)))
    try:
        with pytest.raises(HeaderOnly):
            adapters.decode(SAMPLES["jpeg"][1])
    finally:
        remove()
    assert seen == [("jpeg", "unsupported")]


@pytest.mark.parametrize("name", sorted(SAMPLES))
def test_random_corruption_ends_only_in_named_errors(name):
    """The adapter is called directly, without the registry's conversion of stray exceptions to Malformed."""
    fmt, good = SAMPLES[name]
    det = T.Detected("unknown", fmt, "application/octet-stream", "signature", None, ())
    adapter = adapters.resolve(det)
    rnd = random.Random(zlib.crc32(name.encode()))
    limits = T.Limits(max_seconds=5.0, max_decoded_bytes=64 << 20)
    for trial in range(150):
        bad = bytearray(good)
        mode = trial % 3
        if mode == 0:
            for _ in range(rnd.randint(1, 6)):
                bad[rnd.randrange(len(bad))] = rnd.randrange(256)
        elif mode == 1:
            bad = bad[:rnd.randrange(1, len(bad))]
        else:
            at = rnd.randrange(len(bad))
            bad[at:at] = bytes(rnd.randrange(256) for _ in range(rnd.randint(1, 16)))
        t = time.monotonic()
        try:
            adapter.decode(bytes(bad), det, limits)
        except T.ArtifactError:
            pass
        assert time.monotonic() - t < 6.0


@pytest.mark.parametrize("text", [b"P2 2023 results were good\n", b"Solid state drives have many facets.\n",
                                  b"OFF topic: the meeting moved\n", b"P6 is the sixth paragraph\n"])
def test_ordinary_text_is_not_claimed_by_a_b2_format(text):
    assert detect.detect(text, size=len(text)).format == "text"
