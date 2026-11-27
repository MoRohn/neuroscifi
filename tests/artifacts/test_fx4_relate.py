"""Lane FX4 (ADR-081, second part): the security reviewer's relater constructions (rebuilt in fx4_review_pairs.py)
must never be answered wrongly, and every pair whose truth is "cannot" must get "cannot". Plus the rules R1 (a
comparison decides only when the question names what it measures and where) and R5 (bounded work: 4,000 members in a
few seconds). Lane FX5 withdrew the members, links and times comparisons and made every verdict display-only
(ADR-081): R3's recording test moved to test_fx5_links.py."""
from __future__ import annotations

import hashlib
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fx4_review_pairs as P  # noqa: E402

from neuroscifi.acceptance.omni.subject import relate  # noqa: E402

PAIRS = P.pairs()


@pytest.mark.parametrize("name,files,question,truth", PAIRS, ids=[p[0] for p in PAIRS])
def test_no_reviewer_pair_is_answered_wrongly(name, files, question, truth):
    answer = relate(dict(files), question)["answer"]
    if truth is None:
        assert answer is None, name
    else:
        assert answer in (truth, None), name


def test_a_single_cue_word_never_selects_a_comparison():
    files = {"p.zip": P.zipped(P.PACK), "list.txt": P.size_lines(P.PACK).encode()}
    for question in ("Is the list right for this archive?", "Do the files agree?", "Check the list.",
                     "Do the byte sizes in the list match the files in the archive?"):   # lane FX5: withdrawn
        assert relate(dict(files), question)["answer"] is None, question


def test_a_digest_listing_of_four_thousand_members_is_linear():
    many = {f"d/{i:05d}.txt": b"x%d\n" % i for i in range(4000)}
    sums = "".join(f"{hashlib.sha256(b).hexdigest()}  {n}\n" for n, b in many.items()).encode()
    start = time.process_time()
    answer = relate({"a.zip": P.zipped(many), "SUMS": sums}, "Do the checksums of the archive's files match?")
    assert answer["answer"] in (True, None)
    assert time.process_time() - start < 8.0
