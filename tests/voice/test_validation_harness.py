"""Pure helpers of scripts/voice_real_speech_eval.py (docs/voice/VOICE_VALIDATION.md): text normalisation, edit
operations, speaker-bootstrap WER, Wilson intervals, level and SNR scaling, the telephone channel and utterance
intervals from replayed VAD events. No corpus and no speech service is needed."""
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]


def test_harness_text_and_statistics_helpers():
    pytest.importorskip("scipy")             # the harness is a research tool (optional dependency group "research")
    sys.path.insert(0, str(ROOT / "scripts"))
    try:
        import voice_real_speech_eval as E
    finally:
        sys.path.pop(0)
    assert E.words("Mr. Smith paid $20, um, in 1984!") == ["mister", "smith", "paid", "twenty", "in", "nineteen",
                                                            "eighty", "four"]
    assert E.edit_ops("a b c d".split(), "a x c".split())[:3] == (1, 1, 0)
    rows = [{"speaker": s, "errors": e, "ref_words": 10} for s, e in (("a", 1), ("b", 2), ("c", 0), ("d", 1))]
    point, lo, hi, n = E.bootstrap_wer(rows)
    assert point == pytest.approx(0.1) and lo <= point <= hi and n == 4
    assert E.wilson(0, 10)[0] == 0.0 and E.wilson(10, 10)[1] == 1.0
    import numpy as np
    x = np.sin(np.linspace(0, 2000, 16000)).astype(np.float32) * 0.1
    assert abs(E.active_level(E.set_level(x, -26)) - 10 ** (-26 / 20)) < 1e-3
    noisy = E.add_noise(x, np.random.default_rng(0).normal(0, 1, 32000).astype(np.float32), 10,
                        np.random.default_rng(1))
    noise = noisy - x
    assert 20 * np.log10(E.active_level(x) / np.sqrt(np.mean(noise ** 2))) == pytest.approx(10, abs=0.2)
    tel = E.telephone(x)
    assert len(tel) == len(x)
    utts = E._utterances([[3, "start", False], [12, "confirm", False], [50, "end", True], [60, "start", False],
                          [70, "end", False]])
    assert utts == [(3, 12, 50)]
