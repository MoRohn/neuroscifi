"""Lane FX, item 3: a relater that does not guess (ADR-080). An image or a sound contributes measurements only; a
question is answered only by a comparison it names; words that name the artifacts never focus a comparison; a
disagreement a unit's scale factor would explain, between a bare number and a stated unit, is not a disagreement.
Each test fails without its rule (the image-caption pairs were answered at coin level before)."""
from __future__ import annotations

import numpy as np
import pytest

from neuroscifi.artifacts import adapters, relate as R
import r_pairs as RP


def _rel(files: dict, question: str):
    sides = [R.side(adapters.decode(d, name=n), name=n, data=d) for n, d in files.items()]
    return R.compare(sides[0], sides[1], question)


def _png(w=40, h=30) -> bytes:
    return RP.png(w, h)


def _wav(seconds=2.0, rate=8000) -> bytes:
    import io
    import wave
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        t = np.arange(int(seconds * rate)) / rate
        w.writeframes((0.4 * 32767 * np.sin(2 * np.pi * 330 * t)).astype("<i2").tobytes())
    return buf.getvalue()


@pytest.mark.parametrize("question", ["Does the caption describe the image?", "Is the caption right about the picture?",
                                      "Does the photo show what its caption says?", "Does the caption match the image?",
                                      "Is this the right caption for this picture?", "Is the caption true of this image?"])
@pytest.mark.parametrize("n", [3, 6])
def test_what_an_image_depicts_is_never_answered(question, n):
    rel = _rel({"photo.png": _png(), "caption.txt": f"A 40 x 30 photo of {n} boats.\n".encode()}, question)
    assert rel.answer is None, rel.because()


def test_what_a_sound_says_is_never_answered():
    for q in ("Does the recording say what the transcript says?", "Does the description match the recording?",
              "Is the transcript of this recording right?"):
        rel = _rel({"talk.wav": _wav(), "notes.txt": b"A 2.0 second recording. The speaker says: hello.\n"}, q)
        assert rel.answer is None, (q, rel.because())


def test_a_measured_size_or_duration_the_question_names_is_answered():
    for w, truth in ((40, True), (50, False)):
        rel = _rel({"photo.png": _png(), "caption.txt": f"A {w} x 30 pixel photo.\n".encode()},
                   "Is the image the size the caption states?")
        assert rel.answer is truth
    for s, truth in (("2.0", True), ("3.5", False)):
        rel = _rel({"clip.wav": _wav(), "label.txt": f"The clip lasts {s} seconds.\n".encode()},
                   "Is the recording as long as the label says?")
        assert rel.answer is truth


def test_an_image_s_own_words_are_not_its_statements():
    from neuroscifi.artifacts.types import Raster
    rep = Raster(np.zeros((2, 2), np.uint8), "gray", 8)
    assert R.statements(rep, data=b"# 12 boats\n# total: 5\n") == []


def test_a_bare_number_against_a_stated_unit_does_not_disagree_by_a_scale_factor():
    table = b"item,weight\nA,250\nB,780\n"
    rel = _rel({"report.md": b"# Parts\n\nTotal weight: 1.03 kg.\n", "parts.csv": table},
               "Does the total weight in the report match the weights in the table?")
    assert rel.answer is None
    named = b"item,weight_g\nA,250\nB,780\n"                  # the column's name states grams: compared, and agrees
    rel = _rel({"report.md": b"# Parts\n\nTotal weight: 1.03 kg.\n", "parts.csv": named},
               "Does the total weight in the report match the weights in the table?")
    assert rel.answer is True
