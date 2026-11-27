"""Lane FX5, last part (ADR-081, the owner's decision of 2026-11-27, "Withdraw them"): the three comparisons lanes
FX3-FX5 added (an archive's members against a listing, a drawing's links against a text, a sound's tone times against
a cue sheet) are withdrawn. The adversarial pairs of all three security reviews (rebuilt in this lane's builders:
fx4_review_pairs, fx5_pairs.review_pairs, fx5_pairs.review3_pairs) and the lane's own (fx5_pairs.adversarial_pairs)
stay as tests: no pair is answered wrongly, and no pair that only those comparisons could decide is answered at all."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fx4_review_pairs as P4  # noqa: E402
import fx5_pairs as P5  # noqa: E402

from neuroscifi.acceptance.omni.subject import relate  # noqa: E402
from neuroscifi.artifacts import relate as R  # noqa: E402

ALL = P4.pairs() + P5.review_pairs() + P5.adversarial_pairs() + P5.review3_pairs()
# pairs an older comparison (one that existed before Wave O-3) may still decide: totals, means, extremes, durations,
# image sizes, counts. Every other pair could only be decided by a withdrawn comparison.
OLDER = {"per_item_each", "per_item_every", "svg_millimetres", "named_max_unread_total", "depiction_big",
         "depiction_wide", "count_of_squares", "count_question", "count_question_as_many"}
OLDER |= {n for n, *_ in ALL if n.startswith(("rv_x", "ad_s_"))}


@pytest.mark.parametrize("name,files,question,truth", ALL, ids=[p[0] for p in ALL])
def test_no_pair_is_answered_wrongly_and_withdrawn_kinds_not_at_all(name, files, question, truth):
    answer = relate(dict(files), question)["answer"]
    if name not in OLDER or truth is None:
        assert answer is None, name
    else:
        assert answer in (truth, None), name


@pytest.mark.parametrize("files,question", [
    ({"k.zip": P5.zip_of(P5.KIT), "list.txt": P5.sizes(P5.KIT)}, P5.Q_KIT),
    ({"k.zip": P5.zip_of(P5.KIT), "SHA256SUMS": P5.sums(P5.KIT)}, P5.Q_SUMS),
    ({"w.svg": P5.chart(P5.NODES, P5.WIRES), "w.txt": P5.WIRE_TEXT.encode()}, P5.Q_WIRES),
    ({"b.wav": P5.wav(P5.BEATS), "cues.txt": P5.cues(P5.BEATS, "beep")}, P5.Q_CUES),
])
def test_a_question_of_a_withdrawn_kind_says_so_plainly(files, question):
    out = relate(dict(files), question)
    assert out["answer"] is None and out["because"][0] == R.WITHDRAWN


def test_the_deciding_code_is_gone():
    for name in ("_members_pair", "_links_pair", "_times_pair", "_structure_pairs", "_plain_svg", "_node_links",
                 "_decide_by_structure", "_specific"):
        assert not hasattr(R, name), name
    with pytest.raises(ImportError):
        import neuroscifi.artifacts.relate_grammar  # noqa: F401
